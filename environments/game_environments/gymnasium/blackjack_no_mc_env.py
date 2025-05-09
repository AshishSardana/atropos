#!/usr/bin/env python3
"""
BlackjackEnv: Trainer environment for Gymnasium Blackjack

This wraps Gymnasium's Blackjack-v1 environment to train an LLM via a best-of-n pattern
using function-call style actions. Extends BaseEnv.

Alternative formulation of BlackjackEnv that uses a best-of-n approach to select actions
and no Monte Carlo sampling (direct bonus for winning trajectory). Much faster to train,
but may not be as effective at learning correct strategy (it's effectively a series of bandits).
"""

import json
import logging
import random
from typing import Any, Dict, List, Optional, Tuple, Union

import gymnasium
from tqdm.asyncio import tqdm_asyncio

from atroposlib.envs.base import BaseEnv, BaseEnvConfig, OpenaiConfig, ScoredDataGroup, EvalHandlingEnum
from atroposlib.type_definitions import Message
from atroposlib.utils.tokenize_for_trainer import UNMASKED_ROLES, tokenize_for_trainer
from atroposlib.utils.tool_call_parser import parse_tool_call

logger = logging.getLogger(__name__)


class BlackjackEnvConfig(BaseEnvConfig):
    """
    Configuration for the Blackjack environment trainer.
    """

    env_name: str = "Blackjack-v1"
    temperature: float = 0.7
    top_p: float = 0.9
    max_turns: Optional[int] = 5
    wandb_name: str = "blackjack"

    thinking_active: bool = True
    eval_episodes: int = 100

    batch_size: int = 1024
    max_think_chars_history: int = 3000
    # Should be higher than the max tokens to allow for multiple turns
    max_trajectory_tokens: int = 24576
    debug_mode: bool = False


class BlackjackScoredDataGroup(ScoredDataGroup):
    """
    Represents the scored data for a single step in a Blackjack trajectory, potentially including multiple alternatives.
    """

    seed: int
    tokens: Optional[List[List[int]]] = None
    masks: Optional[List[List[int]]] = None
    scores: Optional[List[float]] = None
    messages: Optional[List[List[Message]]] = None
    parsed_action: Optional[int] = None


class EpisodeState:
    """
    Stores per-episode state: gym env, history, actions, rewards, trajectory.
    """

    def __init__(self, seed: int, env: gymnasium.Env):
        self.seed: int = seed
        self.env: gymnasium.Env = env
        self.message_history: List[Message] = []
        self.actions: List[int] = []
        self.step_rewards: List[float] = []
        self.trajectory: List[BlackjackScoredDataGroup] = []
        self.total_env_reward: float = 0.0
        self.num_correct_actions: int = 0
        self.num_total_actions: int = 0


class BlackjackEnv(BaseEnv):
    """
    Trainer environment for Gymnasium Blackjack using a best-of-n approach with function-call style actions.
    """

    def __init__(
        self,
        config: BlackjackEnvConfig,
        server_configs: List[OpenaiConfig],
        slurm: bool = True,
        testing: bool = False,
    ):
        super().__init__(config, server_configs, slurm, testing)
        self.episodes: Dict[int, EpisodeState] = {}
        self.debug_mode = config.debug_mode
        self.completed_episode_metrics_buffer: List[Dict[str, Any]] = []

        if self.debug_mode:
            logger.setLevel(logging.DEBUG)
        else:
            if logger.level == logging.NOTSET or logger.level > logging.WARNING:
                logger.setLevel(logging.WARNING)

        self.tools = [
            {
                "type": "function",
                "function": {
                    "name": "take_action",
                    "description": "Choose to 'hit' or 'stick' in Blackjack.",
                    "parameters": {
                        "action": {"type": "string", "enum": ["hit", "stick"]}
                    },
                },
            }
        ]

        tools_json = json.dumps(self.tools)
        self.system_prompt = (
            "You are an AI agent playing Blackjack who uses extreme long chains of thought "
            "to carefully consider the probabilities and optimal strategy. "
            "You need to decide whether to hit or stick based on your current hand and the dealer's showing card.\n\n"
            "You should enclose your thoughts and internal monologue inside <think> </think> tags, and then "
            "provide your decision using the take_action function call. You may use extremely long chains "
            "of thought to carefully consider the probabilities and optimal strategy.\n\n"
            f"<tools>\n{tools_json}\n</tools>\n\n"
            "For your function call, return a JSON object with function name and arguments "
            "within <tool_call> </tool_call> tags with the following schema:\n"
            '<tool_call>\n{"arguments": {"action": "hit"}, "name": "take_action"}\n</tool_call>\n\n'
            "Your answer format should be:\n"
            "<think>\n"
            "[Your detailed reasoning process about whether to hit or stick]\n"
            "</think>\n\n"
            '<tool_call>\n{"arguments": {"action": "stick"}, "name": "take_action"}\n</tool_call>\n\n'
            "Remember to carefully consider the probabilities and optimal strategy for Blackjack."
        )

    def _get_or_create_episode(self, seed: int) -> EpisodeState:
        """Retrieve existing or create a new episode state keyed by seed."""
        if seed not in self.episodes:
            env = gymnasium.make(self.config.env_name)
            obs, _ = env.reset(seed=seed)
            ep = EpisodeState(seed, env)
            ep.message_history = [{"role": "system", "content": self.system_prompt}]
            formatted = self._format_observation(obs)
            ep.message_history.append({"role": "environment", "content": formatted})
            self.episodes[seed] = ep
        return self.episodes[seed]

    def _format_observation(self, obs: Tuple[int, int, int]) -> str:
        """Convert Blackjack observation to text for LLM."""
        player_sum, dealer_card, usable_ace = obs
        return (
            f"Your hand sum is {player_sum}. "
            f"Dealer showing: {dealer_card}. "
            f"You have a usable ace: {usable_ace}."
        )

    def _parse_tool_call(self, response: str) -> int:
        """Extract 'hit'/'stick' and map to action 1/0."""
        tool_name, arguments, is_error = parse_tool_call(
            response, self.tools, ["tool_call"]
        )

        logger.warning(
            f"Parsed tool call: name={tool_name}, args={arguments}, error={is_error}"
        )

        if is_error:
            logger.warning(f"Failed to parse tool call from response: {response}")
            return -1

        action = arguments.get("action", "").lower()
        if action == "hit":
            return 1
        elif action == "stick":
            return 0
        else:
            logger.warning(f"Invalid action value: {action}")
            return -1

    def _score_response(
        self,
        env_reward: float,
        response_text: str,
        parsed_action: int,
        episode_seed: int,
    ) -> float:
        """
        Calculates a score for a single agent response based purely on environment reward
        and a penalty for invalid action format.
        """
        current_env_reward = env_reward

        if parsed_action == -1:
            current_env_reward -= 0.5
            logger.debug(
                f"[_score_response Seed: {episode_seed}] Penalty applied for invalid action format (-0.5)."
            )

        final_score = current_env_reward

        logger.debug(
            f"[_score_response Seed: {episode_seed}] Final Score Calculation: "
            f"Env Reward (raw): {env_reward:.4f}, "
            f"Env Reward (adjusted for invalid): {current_env_reward:.4f}, "
            f"==> Final Score (from env): {final_score:.4f}"
        )
        return final_score

    async def _select_best_action(
        self, episode: EpisodeState, actions: List[int], responses: List[str]
    ) -> Tuple[int, List[float]]:
        """
        Simulates and scores multiple candidate actions to select the best one.

        Args:
            episode: The current episode state.
            actions: A list of parsed actions (0, 1, or -1) corresponding to the responses.
            responses: A list of full agent responses (<think>...</think><tool_call>...</tool_call>).

        Returns:
            A tuple containing:
                - The best action selected (0, 1, or -1).
                - A list of scores for each action/response.
        """
        if len(actions) != len(responses):
            logger.error(
                f"[_select_best_action Seed: {episode.seed}] "
                f"Mismatch between actions ({len(actions)}) and responses ({len(responses)}) count."
            )
            default_action = next((a for a in actions if a != -1), -1)
            return default_action, [-10.0] * len(actions)

        scores = [0.0] * len(actions)
        token_lengths = [0] * len(actions)

        try:
            for idx, (action, response_text) in enumerate(zip(actions, responses)):
                sim_env = gymnasium.make(self.config.env_name)
                sim_obs, sim_info = sim_env.reset(seed=episode.seed)
                valid_sim = True
                for past_action in episode.actions:
                    sim_obs, _, term, trunc, sim_info = sim_env.step(past_action)
                    if term or trunc:
                        logger.warning(
                            f"[_select_best_action Seed: {episode.seed}] "
                            f"Episode terminated during history replay before simulating action {idx}. "
                            f"Assigning low score."
                        )
                        valid_sim = False
                        break
                if not valid_sim:
                    scores[idx] = -10.0
                    continue

                if action == -1:
                    # Penalty for parsing error is applied within _score_response.
                    env_reward_sim = 0.0
                else:
                    _obs_sim, env_reward_sim, term_sim, trunc_sim, _info_sim = (
                        sim_env.step(action)
                    )
                    logger.debug(
                        f"[_select_best_action Seed: {episode.seed}] Sim Action {idx} "
                        f"(val:{action}) -> Reward:{env_reward_sim}, Term:{term_sim}"
                    )

                combined_score = self._score_response(
                    env_reward=env_reward_sim,
                    response_text=response_text,
                    parsed_action=action,
                    episode_seed=episode.seed,
                )
                scores[idx] = combined_score
                token_lengths[idx] = len(self.tokenizer.encode(response_text))

        except Exception as e:
            logger.exception(
                f"[_select_best_action Seed: {episode.seed}] "
                f"Error during action simulation/scoring: {e}"
            )
            default_action = next((a for a in actions if a != -1), -1)
            return default_action, [-10.0] * len(actions)

        best_score = float("-inf")
        best_action = -1
        best_action_idx = -1

        if scores:
            best_score = max(scores)
            potential_best_indices = [
                i for i, score in enumerate(scores) if score == best_score
            ]

            valid_indices = [i for i in potential_best_indices if actions[i] != -1]
            if valid_indices:
                if len(valid_indices) > 1:
                    try:
                        best_action_idx = min(
                            valid_indices, key=lambda i: token_lengths[i]
                        )
                        logger.debug(
                            f"[_select_best_action Seed: {episode.seed}] "
                            f"Tie-breaking valid actions based on token length. Chosen index: {best_action_idx}"
                        )
                    except IndexError:
                        logger.warning(
                            f"[_select_best_action Seed: {episode.seed}] "
                            f"IndexError during token length tie-breaking. Defaulting to first valid index."
                        )
                        best_action_idx = valid_indices[0]
                else:
                    best_action_idx = valid_indices[0]
            elif potential_best_indices:
                best_action_idx = potential_best_indices[0]
                logger.debug(
                    f"[_select_best_action Seed: {episode.seed}] "
                    f"All best scores correspond to invalid actions. Choosing first: index {best_action_idx}"
                )
            else:
                logger.error(
                    f"[_select_best_action Seed: {episode.seed}] "
                    f"No potential best indices found despite scores existing. Returning default action -1."
                )
                best_action_idx = -1

            if best_action_idx != -1:
                best_action = actions[best_action_idx]
            else:
                best_action = -1

            logger.info(
                f"[_select_best_action Seed: {episode.seed}] Selected action: {best_action} "
                f"(Index: {best_action_idx}, "
                f"Score: {scores[best_action_idx] if best_action_idx != -1 else 'N/A'}) "
                f"from scores: {['{:.4f}'.format(s) for s in scores]}"
            )
        else:
            logger.error(
                f"[_select_best_action Seed: {episode.seed}] No scores calculated. Returning default action -1."
            )

        return best_action, scores

    async def collect_trajectory(self, seed: int) -> List[BlackjackScoredDataGroup]:
        """
        Run a single episode from the given seed, using a best-of-n approach each step.
        Refactored to use _select_best_action.
        Returns a list of BlackjackScoredDataGroup, one per time step.
        """
        ep = self._get_or_create_episode(seed)
        max_turns = self.config.max_turns if self.config.max_turns is not None else 5
        logger.info(
            f"[Collect Trajectory Seed: {seed}] Starting episode. Max turns: {max_turns}"
        )

        for turn in range(max_turns):
            logger.debug(
                f"[Collect Trajectory Seed: {seed}] Starting Turn {turn + 1}/{max_turns}"
            )
            messages_for_prompt = ep.message_history.copy()

            if self.config.thinking_active:
                messages_for_prompt.append({"role": "agent", "content": "<think>\n"})
            else:
                messages_for_prompt.append({"role": "agent", "content": ""})

            prompt = self.tokenizer.apply_chat_template(
                messages_for_prompt, tokenize=False
            )
            logger.debug(
                f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] Prompting LLM..."
            )

            try:
                completions = await self.server.completion(
                    prompt=prompt,
                    n=self.config.group_size,
                    max_tokens=self.config.max_token_length,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                )
            except Exception as api_error:
                logger.exception(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"API Error during self.server.completion: {api_error}"
                )
                return self._ensure_trajectory_token_limit(ep.trajectory)

            if (
                not completions
                or not completions.choices
                or len(completions.choices) != self.config.group_size
            ):
                logger.error(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"API did not return the expected number of choices "
                    f"({self.config.group_size} vs {len(completions.choices) if completions else 0}). "
                    f"Aborting episode."
                )
                return self._ensure_trajectory_token_limit(ep.trajectory)

            alt_actions: List[int] = []
            alt_responses: List[str] = []
            for choice_idx, choice in enumerate(completions.choices):
                response_text = (
                    choice.text
                    if hasattr(choice, "text")
                    else getattr(choice.message, "content", "")
                )
                full_response = (
                    ("<think>\n" + response_text)
                    if self.config.thinking_active
                    else response_text
                )
                alt_responses.append(full_response)

                parsed_act = self._parse_tool_call(full_response)
                alt_actions.append(parsed_act)
                logger.debug(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"Choice {choice_idx}: Parsed Action={parsed_act}, Response Length={len(full_response)}"
                )

            logger.debug(
                f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] Selecting best action..."
            )
            best_action, scores = await self._select_best_action(
                ep, alt_actions, alt_responses
            )

            best_action_idx = -1
            try:
                best_score_val = max(scores)
                possible_indices = [
                    i
                    for i, (act, score) in enumerate(zip(alt_actions, scores))
                    if act == best_action and score == best_score_val
                ]
                if possible_indices:
                    best_action_idx = possible_indices[0]
                    logger.info(
                        f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                        f"Best action selected: {best_action} "
                        f"(Index: {best_action_idx}), "
                        f"Score: {scores[best_action_idx]:.4f}"
                    )
                else:
                    logger.warning(
                        f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                        f"Could not find index for best action {best_action} with score {best_score_val}. "
                        f"Trying first occurrence of action."
                    )
                    best_action_idx = alt_actions.index(best_action)
                    logger.info(
                        f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                        f"Fallback - Best action selected: {best_action} (Index: {best_action_idx}), "
                        f"Score: {scores[best_action_idx]:.4f}"
                    )

                best_response = alt_responses[best_action_idx]
            except (ValueError, IndexError) as e:
                logger.error(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"Error finding index for best action {best_action}: {e}. "
                    f"Cannot proceed with episode."
                )
                if seed in self.episodes:
                    try:
                        self.episodes[seed].env.close()
                    except Exception as close_exc:
                        logger.warning(
                            f"[Collect Trajectory Seed: {seed}] "
                            f"Exception closing env for aborted episode on "
                            f"best_action index error: {close_exc}"
                        )
                    del self.episodes[seed]
                return self._ensure_trajectory_token_limit(ep.trajectory)

            alt_tokens: List[List[int]] = []
            alt_masks: List[List[int]] = []
            alt_messages: List[List[Message]] = []
            tokenization_failed_for_step = False
            for response in alt_responses:
                step_msgs: List[Message] = [
                    {"role": m["role"], "content": m["content"]}
                    for m in ep.message_history
                ]
                step_msgs.append({"role": "agent", "content": response})

                try:
                    out = tokenize_for_trainer(self.tokenizer, step_msgs)
                    alt_tokens.append(out["tokens"])
                    alt_masks.append(out["masks"])
                    alt_messages.append(step_msgs)
                except Exception as tokenization_error:
                    logger.exception(
                        f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                        f"Critical tokenization error for response: {response[:100]}... "
                        f"Error: {tokenization_error}. Aborting episode."
                    )
                    tokenization_failed_for_step = True
                    break

            if tokenization_failed_for_step:
                logger.warning(
                    f"[Collect Trajectory Seed: {seed}] Episode aborted at turn {turn+1} due to tokenization failure."
                )
                if seed in self.episodes:
                    try:
                        self.episodes[seed].env.close()
                    except Exception as e:
                        logger.warning(
                            f"[Collect Trajectory Seed: {seed}] Exception closing env for aborted episode: {e}"
                        )
                    del self.episodes[seed]
                return self._ensure_trajectory_token_limit(ep.trajectory)

            expected_len = self.config.group_size
            if len(alt_tokens) != expected_len:
                alt_tokens.extend([[]] * (expected_len - len(alt_tokens)))
            if len(alt_masks) != expected_len:
                alt_masks.extend([[]] * (expected_len - len(alt_masks)))
            if len(alt_messages) != expected_len:
                alt_messages.extend(
                    [
                        [
                            {
                                "role": "system",
                                "content": "Missing due to prior success but unexpected count",
                            }
                        ]
                    ]
                    * (expected_len - len(alt_messages))
                )

            env_action = 0 if best_action == -1 else best_action
            if best_action == -1:
                logger.warning(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"Selected action was invalid format (-1). "
                    f"Stepping env with 'stick' (0)."
                )

            try:
                obs, reward, term, trunc, info = ep.env.step(env_action)
                logger.info(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"Stepped main env with action {env_action}. "
                    f"Reward: {reward}, Term: {term}, Trunc: {trunc}"
                )
            except Exception as env_step_error:
                logger.exception(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                    f"Error stepping main environment with action {env_action}: {env_step_error}"
                )
                term = True
                reward = -1.0
                obs = None

            ep.actions.append(env_action)
            ep.step_rewards.append(reward)

            ep.total_env_reward += reward

            ep.num_total_actions += 1
            if best_action != -1:
                ep.num_correct_actions += 1

            logger.info(
                f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] "
                f"Step Rewards: Env={reward:.2f}. "
                f"Running Totals: Env={ep.total_env_reward:.2f}."
            )

            ep.trajectory.append(
                BlackjackScoredDataGroup(
                    overrides=[],
                    seed=seed,
                    tokens=alt_tokens,
                    masks=alt_masks,
                    scores=scores,
                    messages=alt_messages,
                    parsed_action=best_action,
                )
            )

            if term or trunc:
                logger.info(
                    f"[Collect Trajectory Seed: {seed}] "
                    f"Episode ended. Term={term}, Trunc={trunc}. "
                    f"Final Reward: {reward}"
                )
                ep.message_history.append({"role": "agent", "content": best_response})

                if obs is not None:
                    final_formatted_obs = self._format_observation(obs)
                    logger.debug(
                        f"[Collect Trajectory Seed: {seed}] "
                        f"Final State: {final_formatted_obs} (Reward: {reward})"
                    )
                else:
                    logger.debug(
                        f"[Collect Trajectory Seed: {seed}] "
                        f"Episode terminated with error. (Reward: {reward})"
                    )

                break
            else:
                response_for_history = self._truncate_thinking_for_history(
                    best_response, self.config.max_think_chars_history
                )
                ep.message_history.append(
                    {"role": "agent", "content": response_for_history}
                )
                formatted_obs = self._format_observation(obs)
                ep.message_history.append(
                    {"role": "environment", "content": formatted_obs}
                )
                logger.debug(
                    f"[Collect Trajectory Seed: {seed} Turn: {turn+1}] New Observation: {formatted_obs}"
                )

        logger.info(
            f"[Collect Trajectory Seed: {seed}] "
            f"Finished episode after {len(ep.actions)} steps."
        )
        logger.info(
            f"[Collect Trajectory Seed: {seed}] "
            f"Final Totals: Env Reward={ep.total_env_reward:.2f}."
        )
        logger.info(
            f"[Collect Trajectory Seed: {seed}] "
            f"Action Accuracy: {ep.num_correct_actions}/{max(1, ep.num_total_actions)} "
            f"({ep.num_correct_actions/max(1, ep.num_total_actions):.2%})"
        )

        final_env_reward_for_outcome = 0
        if ep.step_rewards:
            final_env_reward_for_outcome = ep.step_rewards[-1]
        game_outcome = 0
        if final_env_reward_for_outcome > 0:
            game_outcome = 1
        elif final_env_reward_for_outcome < 0:
            game_outcome = -1

        episode_summary_metrics = {
            "seed": seed,
            "total_env_reward": ep.total_env_reward,
            "num_correct_actions": ep.num_correct_actions,
            "num_total_actions": ep.num_total_actions,
            "game_outcome": game_outcome,
            "num_steps_in_episode": len(ep.actions),
        }
        self.completed_episode_metrics_buffer.append(episode_summary_metrics)

        if seed in self.episodes:
            try:
                self.episodes[seed].env.close()
            except Exception as e:
                logger.warning(
                    f"[Collect Trajectory Seed: {seed}] "
                    f"Exception closing env for episode: {e}"
                )
            del self.episodes[seed]
            logger.debug(
                f"[Collect Trajectory Seed: {seed}] "
                f"Cleared episode state from self.episodes."
            )

        return self._ensure_trajectory_token_limit(ep.trajectory)

    async def collect_trajectories(
        self, item: Tuple[int, int]
    ) -> Tuple[List[BlackjackScoredDataGroup], List[Tuple[int, int]]]:
        seed, _ = item
        traj = await self.collect_trajectory(seed)
        if traj:
            traj = self._ensure_trajectory_token_limit(traj)

        if not traj:
            logger.warning(
                f"[collect_trajectories] "
                f"All steps for seed {seed} were filtered out due to token limit "
                f"constraints. Returning empty trajectory."
            )

        return traj, []

    async def score(
        self,
        rollout_group_data: List[BlackjackScoredDataGroup],
    ) -> List[Optional[BlackjackScoredDataGroup]]:
        """
        Applies final scoring adjustments to a completed trajectory.
        If the game was a win (determined by replaying chosen actions), a bonus
        is applied to the best alternative at each step.
        Tie-breaking based on token length is applied subsequently.

        Args:
            rollout_group_data: The list of ScoredDataGroups representing the trajectory.

        Returns:
            The list of ScoredDataGroups with potentially adjusted scores.
            Returns a list containing None elements if input steps are invalid.
        """
        logger.info(
            f"score: Received rollout_group_data with {len(rollout_group_data)} "
            f"groups for scoring."
        )

        if not rollout_group_data:
            logger.warning(
                "score: Received empty rollout_group_data. Returning empty list."
            )
            return []

        # Ensure all elements are at least dictionaries before proceeding
        if not all(
            isinstance(rgd, dict) for rgd in rollout_group_data if rgd is not None
        ):
            logger.error(
                "score: rollout_group_data contains non-dictionary elements. "
                "Cannot proceed."
            )
            # Return a list of Nones matching input length or handle as error
            return [None] * len(rollout_group_data)

        # 1. Determine Overall Game Outcome
        final_env_reward_for_outcome = 0.0
        is_win = False
        seed_for_outcome = None
        first_valid_step_for_seed = next(
            (rgd for rgd in rollout_group_data if rgd is not None and "seed" in rgd),
            None,
        )

        if not first_valid_step_for_seed:
            logger.warning(
                "score: Cannot determine game outcome, no valid step with seed found "
                "in rollout_group_data."
            )
        else:
            seed_for_outcome = first_valid_step_for_seed["seed"]
            logger.info(
                f"score [Seed: {seed_for_outcome}]: Starting game outcome replay."
            )
            try:
                temp_env_outcome = gymnasium.make(self.config.env_name)
                temp_obs_outcome, _ = temp_env_outcome.reset(seed=seed_for_outcome)

                for step_idx_outcome, step_group_for_outcome in enumerate(
                    rollout_group_data
                ):
                    if step_group_for_outcome is None:
                        logger.warning(
                            f"score [Seed: {seed_for_outcome}]: "
                            f"Encountered None step_group at index {step_idx_outcome} "
                            f"during outcome replay. Assuming non-win."
                        )
                        final_env_reward_for_outcome = 0.0
                        break

                    action_for_outcome_step = step_group_for_outcome.get(
                        "parsed_action"
                    )

                    if action_for_outcome_step is None or action_for_outcome_step == -1:
                        logger.warning(
                            f"score [Seed: {seed_for_outcome}]: "
                            f"Invalid action ({action_for_outcome_step}) found at step {step_idx_outcome} "
                            f"during game outcome replay. Assuming non-win outcome for scoring."
                        )
                        final_env_reward_for_outcome = 0.0  # Treat as non-win
                        break

                    logger.debug(
                        f"score [Seed: {seed_for_outcome} Replay]: "
                        f"Step {step_idx_outcome}, Action: {action_for_outcome_step}"
                    )
                    (
                        temp_obs_outcome,
                        step_reward_outcome,
                        term_outcome,
                        trunc_outcome,
                        _,
                    ) = temp_env_outcome.step(action_for_outcome_step)
                    final_env_reward_for_outcome = step_reward_outcome

                    if term_outcome or trunc_outcome:
                        logger.info(
                            f"score [Seed: {seed_for_outcome}]: "
                            f"Game outcome replay ended at step {step_idx_outcome} "
                            f"(action: {action_for_outcome_step}). "
                            f"Final env reward for outcome: {final_env_reward_for_outcome}"
                        )
                        break
                else:  # Loop completed without break
                    logger.info(
                        f"score [Seed: {seed_for_outcome}]: "
                        f"Game outcome replay completed all steps. "
                        f"Final env reward for outcome: {final_env_reward_for_outcome}"
                    )

                temp_env_outcome.close()
            except Exception as e:
                logger.exception(
                    f"score [Seed: {seed_for_outcome}]: "
                    f"Error during game outcome replay: {e}. "
                    f"Assuming non-win."
                )
                final_env_reward_for_outcome = 0.0

            if final_env_reward_for_outcome > 0:
                is_win = True
                logger.info(
                    f"score [Seed: {seed_for_outcome}]: "
                    f"Game outcome determined as WIN "
                    f"(Final Env Reward: {final_env_reward_for_outcome}). "
                    f"Win bonus (+1.0) will be applied to best alternative at each step."
                )
            else:
                logger.info(
                    f"score [Seed: {seed_for_outcome}]: "
                    f"Game outcome determined as NON-WIN "
                    f"(Final Env Reward: {final_env_reward_for_outcome}). "
                    f"No win bonus from game outcome will be applied."
                )

        processed_rollout_data: List[Optional[BlackjackScoredDataGroup]] = []

        for step_idx, original_step_group_untyped in enumerate(rollout_group_data):
            if original_step_group_untyped is None:
                logger.warning(f"score: Skipping None step_group at index {step_idx}.")
                processed_rollout_data.append(None)
                continue

            # Make a copy to modify scores
            current_step_group: BlackjackScoredDataGroup = (
                original_step_group_untyped.copy()
            )
            step_seed = current_step_group.get(
                "seed", "N/A"
            )  # Use N/A if seed is somehow missing

            if current_step_group.get("scores") is None:
                logger.warning(
                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                    f"Scores are missing. Cannot apply win bonus or tie-breaking."
                )
                processed_rollout_data.append(
                    current_step_group
                )  # Append original or a copy
                continue

            # Ensure scores is a list of numbers
            original_scores = current_step_group["scores"]
            if not isinstance(original_scores, list) or not all(
                isinstance(s, (int, float)) for s in original_scores
            ):
                logger.warning(
                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                    f"'scores' is not a list of numbers. "
                    f"Skipping scoring for this step. Scores: {original_scores}"
                )
                processed_rollout_data.append(current_step_group)
                continue

            modified_scores = original_scores.copy()  # Work on a copy

            # 2. Apply Win Bonus (if applicable)
            if is_win and modified_scores:  # Ensure scores list is not empty
                try:
                    max_score_in_step = -float("inf")
                    # Find max score correctly, even with Nones, though previous check should handle Nones in list
                    valid_scores_for_max = [
                        s for s in modified_scores if isinstance(s, (int, float))
                    ]
                    if not valid_scores_for_max:
                        logger.warning(
                            f"score [Seed: {step_seed}, Step: {step_idx}]: "
                            f"No valid numeric scores found to determine best alternative for win bonus."
                        )
                    else:
                        max_score_in_step = max(valid_scores_for_max)
                        # Find first index of max_score_in_step. If multiple, bonus applies to first.
                        best_alternative_idx_this_step = -1
                        for idx, score_val in enumerate(modified_scores):
                            if score_val == max_score_in_step:
                                best_alternative_idx_this_step = idx
                                break

                        if best_alternative_idx_this_step != -1:
                            win_bonus_amount = 1.0
                            modified_scores[
                                best_alternative_idx_this_step
                            ] += win_bonus_amount
                            logger.info(
                                f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                f"Applied WIN bonus ({win_bonus_amount}) to alternative "
                                f"{best_alternative_idx_this_step} "
                                f"(Original score: {original_scores[best_alternative_idx_this_step]:.4f}, "
                                f"New: {modified_scores[best_alternative_idx_this_step]:.4f})."
                            )
                        else:
                            logger.warning(
                                f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                f"Could not find index of max score {max_score_in_step} for win bonus. "
                                f"This should not happen if scores exist."
                            )
                except (
                    ValueError
                ):  # Should be caught by empty list check or valid_scores_for_max
                    # Split into two lines to avoid line length issues
                    score_msg = (
                        f"score [Seed: {step_seed}, Step: {step_idx}]: "
                        f"Error finding max score for win bonus."
                    )
                    logger.warning(score_msg)
                    logger.debug(f"Problematic scores: {modified_scores}")
                except Exception as e_bonus:
                    logger.exception(
                        f"score [Seed: {step_seed}, Step: {step_idx}]: "
                        f"Unexpected error applying win bonus: {e_bonus}"
                    )

            # 3. Apply Tie-Breaking Logic (to potentially bonus-adjusted scores)
            step_messages = current_step_group.get("messages")
            if not isinstance(step_messages, list) or len(modified_scores) != len(
                step_messages
            ):
                logger.warning(
                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                    f"Mismatch between scores ({len(modified_scores)}) and messages "
                    f"({len(step_messages) if isinstance(step_messages, list) else 'not a list'}) "
                    f"lengths, or messages missing. Skipping tie-breaking for this step."
                )
            elif modified_scores:  # Ensure scores list is not empty for tie-breaking
                token_lengths = []
                valid_messages_for_tiebreak = True
                for alt_msg_list_idx, alt_msg_list in enumerate(step_messages):
                    if (
                        not isinstance(alt_msg_list, list)
                        or not alt_msg_list
                        or not isinstance(alt_msg_list[-1], dict)
                        or "content" not in alt_msg_list[-1]
                    ):
                        logger.warning(
                            f"score [Seed: {step_seed}, Step: {step_idx}]: "
                            f"Invalid message structure for alternative {alt_msg_list_idx} "
                            f"during tie-breaking. Skipping tie-breaking for this step."
                        )
                        valid_messages_for_tiebreak = False
                        break
                    response_text = alt_msg_list[-1]["content"]
                    try:
                        token_lengths.append(len(self.tokenizer.encode(response_text)))
                    except Exception as e_tok:
                        logger.error(
                            f"score [Seed: {step_seed}, Step: {step_idx}]: "
                            f"Tokenization error for tie-breaking on alt {alt_msg_list_idx}: {e_tok}. "
                            f"Defaulting token length to large value."
                        )
                        token_lengths.append(
                            float("inf")
                        )  # Penalize if tokenization fails

                if valid_messages_for_tiebreak:
                    score_groups = {}  # Maps score_value to list of indices
                    for idx, score_val in enumerate(modified_scores):
                        if not isinstance(
                            score_val, (int, float)
                        ):  # Skip non-numeric scores
                            continue
                        if score_val not in score_groups:
                            score_groups[score_val] = []
                        score_groups[score_val].append(idx)

                    scores_after_tiebreak = modified_scores.copy()
                    for score_val, indices_with_this_score in score_groups.items():
                        if len(indices_with_this_score) > 1:  # A tie is found
                            # Check if token_lengths are available for all tied indices
                            if not all(
                                idx < len(token_lengths)
                                for idx in indices_with_this_score
                            ):
                                logger.warning(
                                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                    f"Token length data incomplete for tied score {score_val}. "
                                    f"Indices: {indices_with_this_score}, "
                                    f"Token lengths count: {len(token_lengths)}. "
                                    f"Skipping tie-break for this group."
                                )
                                continue

                            try:
                                # Sort tied indices by their token_lengths
                                sorted_tied_indices = sorted(
                                    indices_with_this_score,
                                    key=lambda i: token_lengths[i],
                                )

                                # Apply penalty to all but the first (shortest token length)
                                for rank, tied_idx in enumerate(
                                    sorted_tied_indices[1:], 1
                                ):  # Start rank from 1 for 2nd shortest
                                    penalty = 0.0001 * rank
                                    scores_after_tiebreak[tied_idx] -= penalty
                                    logger.debug(
                                        f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                        f"Applied tie-break penalty {-penalty:.5f} to alternative index {tied_idx} "
                                        f"(original tied score {score_val:.4f}, token length rank {rank})."
                                    )
                            except IndexError:
                                logger.warning(
                                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                    f"IndexError during tie-breaking for score {score_val}. "
                                    f"Indices: {indices_with_this_score}. "
                                    f"Skipping tie-break for this group."
                                )
                            except Exception as e_tiebreak:
                                logger.exception(
                                    f"score [Seed: {step_seed}, Step: {step_idx}]: "
                                    f"Unexpected error during tie-breaking for score {score_val}: {e_tiebreak}"
                                )
                    modified_scores = scores_after_tiebreak

            current_step_group["scores"] = modified_scores
            processed_rollout_data.append(current_step_group)

        logger.info(
            f"score: Finished scoring. Processed {len(processed_rollout_data)} step groups."
        )
        return processed_rollout_data

    async def setup(self):
        pass

    async def get_next_item(self) -> Tuple[int, int]:
        import random

        return (random.randint(0, 1000000), 0)

    async def rollout_and_score_eval(self, seed: int) -> Dict[str, Any]:
        """
        Run a single episode for evaluation and return detailed metrics.
        Does not use the best-of-n sampling, but a single completion per step.
        Cleans up the episode state after completion.
        """
        ep = self._get_or_create_episode(seed)
        max_turns = self.config.max_turns if self.config.max_turns is not None else 5
        logger.info(
            f"[Eval Rollout Seed: {seed}] Starting episode. Max turns: {max_turns}"
        )

        episode_metrics = {
            "seed": seed,
            "total_env_reward": 0.0,
            "num_turns": 0,
            "num_correct_actions": 0,
            "num_invalid_actions": 0,
            "actions_chosen": [],
            "game_outcome": 0,
        }

        for turn in range(max_turns):
            episode_metrics["num_turns"] = turn + 1
            messages_for_prompt = ep.message_history.copy()

            if self.config.thinking_active:
                messages_for_prompt.append({"role": "agent", "content": "<think>\n"})
            else:
                messages_for_prompt.append({"role": "agent", "content": ""})

            prompt = self.tokenizer.apply_chat_template(
                messages_for_prompt, tokenize=False
            )

            try:
                completions = await self.server.completion(
                    prompt=prompt,
                    n=1,
                    max_tokens=self.config.max_token_length,
                    temperature=self.config.temperature,
                    top_p=self.config.top_p,
                    split="eval",
                )
            except Exception as api_error:
                logger.exception(
                    f"[Eval Rollout Seed: {seed} Turn: {turn+1}] API Error: {api_error}"
                )
                break

            if not completions or not completions.choices:
                logger.error(
                    f"[Eval Rollout Seed: {seed} Turn: {turn+1}] API did not return any choices. Aborting episode."
                )
                break

            response_text = (
                completions.choices[0].text
                if hasattr(completions.choices[0], "text")
                else getattr(completions.choices[0].message, "content", "")
            )
            full_response = (
                ("<think>\n" + response_text)
                if self.config.thinking_active
                else response_text
            )

            parsed_action = self._parse_tool_call(full_response)
            episode_metrics["actions_chosen"].append(parsed_action)

            if parsed_action == -1:
                episode_metrics["num_invalid_actions"] += 1
                env_action = 0
                logger.warning(
                    f"[Eval Rollout Seed: {seed} Turn: {turn+1}] Invalid action parsed. Defaulting to 'stick'."
                )
            else:
                episode_metrics["num_correct_actions"] += 1
                env_action = parsed_action

            try:
                obs, reward, term, trunc, info = ep.env.step(env_action)
            except Exception as env_step_error:
                logger.exception(
                    f"[Eval Rollout Seed: {seed} Turn: {turn+1}] Error stepping env: {env_step_error}"
                )
                term = True
                reward = -1.0
                obs = None

            ep.actions.append(env_action)
            ep.step_rewards.append(reward)

            ep.total_env_reward += reward

            if term or trunc:
                episode_metrics["game_outcome"] = int(reward)
                logger.info(
                    f"[Eval Rollout Seed: {seed}] Episode ended. Outcome Reward: {reward}"
                )

                ep.message_history.append({"role": "agent", "content": full_response})

                if obs is not None:
                    final_formatted_obs = self._format_observation(obs)
                    logger.debug(
                        f"[Eval Rollout Seed: {seed}] "
                        f"Final State: {final_formatted_obs} (Reward: {reward})"
                    )
                else:
                    logger.debug(
                        f"[Eval Rollout Seed: {seed}] "
                        f"Episode terminated with error. (Reward: {reward})"
                    )

                break
            else:
                ep.message_history.append({"role": "agent", "content": full_response})
                formatted_obs = self._format_observation(obs)
                ep.message_history.append(
                    {"role": "environment", "content": formatted_obs}
                )

        logger.info(
            f"[Eval Rollout Seed: {seed}] Finished episode. Metrics: {episode_metrics}"
        )

        if seed in self.episodes:
            try:
                self.episodes[seed].env.close()
            except Exception as e:
                logger.warning(
                    f"[Eval Rollout Seed: {seed}] Exception closing env for episode: {e}"
                )
            del self.episodes[seed]

        return episode_metrics

    async def evaluate(self, *args, **kwargs):
        """Run evaluation episodes and aggregate metrics for logging."""
        if not self.config.use_wandb:
            logger.info("Skipping evaluation as wandb is not enabled.")
            return

        num_eval_episodes = self.config.eval_episodes
        logger.info(f"Starting evaluation for {num_eval_episodes} episodes.")

        eval_tasks = []

        for i in range(num_eval_episodes):
            eval_seed = random.randint(1000001, 2000000)
            eval_tasks.append(self.rollout_and_score_eval(eval_seed))

        all_episode_metrics = await tqdm_asyncio.gather(*eval_tasks)

        if not all_episode_metrics:
            logger.warning("No metrics collected from evaluation episodes.")
            return

        valid_metrics = [m for m in all_episode_metrics if m is not None]
        if not valid_metrics:
            logger.warning("All evaluation episodes resulted in None metrics.")
            return

        num_completed_episodes = len(valid_metrics)

        avg_total_env_reward = (
            sum(m["total_env_reward"] for m in valid_metrics) / num_completed_episodes
        )
        avg_num_turns = (
            sum(m["num_turns"] for m in valid_metrics) / num_completed_episodes
        )

        total_correct_actions = sum(m["num_correct_actions"] for m in valid_metrics)
        total_invalid_actions = sum(m["num_invalid_actions"] for m in valid_metrics)
        total_actions_taken = total_correct_actions + total_invalid_actions
        action_accuracy = (
            total_correct_actions / total_actions_taken
            if total_actions_taken > 0
            else 0
        )
        invalid_action_rate = (
            total_invalid_actions / total_actions_taken
            if total_actions_taken > 0
            else 0
        )

        wins = sum(1 for m in valid_metrics if m["game_outcome"] == 1)
        losses = sum(1 for m in valid_metrics if m["game_outcome"] == -1)
        draws = sum(1 for m in valid_metrics if m["game_outcome"] == 0)

        win_rate = wins / num_completed_episodes if num_completed_episodes > 0 else 0
        loss_rate = losses / num_completed_episodes if num_completed_episodes > 0 else 0
        draw_rate = draws / num_completed_episodes if num_completed_episodes > 0 else 0

        all_chosen_actions = [
            action for m in valid_metrics for action in m["actions_chosen"]
        ]
        count_hit = sum(1 for act in all_chosen_actions if act == 1)
        count_stick = sum(1 for act in all_chosen_actions if act == 0)
        count_error_actions = sum(1 for act in all_chosen_actions if act == -1)
        total_parsed_actions_in_eval = len(all_chosen_actions)

        self.eval_metrics = [
            ("eval/avg_total_env_reward", avg_total_env_reward),
            ("eval/avg_num_turns", avg_num_turns),
            ("eval/action_accuracy", action_accuracy),
            ("eval/invalid_action_rate", invalid_action_rate),
            ("eval/win_rate", win_rate),
            ("eval/loss_rate", loss_rate),
            ("eval/draw_rate", draw_rate),
            ("eval/num_wins", wins),
            ("eval/num_losses", losses),
            ("eval/num_draws", draws),
            ("eval/num_completed_episodes", num_completed_episodes),
            (
                "eval/hit_chosen_rate",
                (
                    count_hit / total_parsed_actions_in_eval
                    if total_parsed_actions_in_eval > 0
                    else 0
                ),
            ),
            (
                "eval/stick_chosen_rate",
                (
                    count_stick / total_parsed_actions_in_eval
                    if total_parsed_actions_in_eval > 0
                    else 0
                ),
            ),
            (
                "eval/error_action_chosen_rate",
                (
                    count_error_actions / total_parsed_actions_in_eval
                    if total_parsed_actions_in_eval > 0
                    else 0
                ),
            ),
        ]

        logger.info(f"Evaluation completed. Aggregated metrics: {self.eval_metrics}")

    async def wandb_log(self, wandb_metrics: Optional[Dict[str, Any]] = None):
        """
        Log aggregated metrics from completed training episodes and call super().wandb_log.
        """
        if wandb_metrics is None:
            wandb_metrics = {}

        if self.completed_episode_metrics_buffer:
            num_episodes_in_buffer = len(self.completed_episode_metrics_buffer)

            avg_ep_env_reward = (
                sum(
                    m["total_env_reward"] for m in self.completed_episode_metrics_buffer
                )
                / num_episodes_in_buffer
            )

            total_ep_correct_actions = sum(
                m["num_correct_actions"] for m in self.completed_episode_metrics_buffer
            )
            total_ep_actions = sum(
                m["num_total_actions"] for m in self.completed_episode_metrics_buffer
            )
            avg_ep_action_accuracy = (
                total_ep_correct_actions / total_ep_actions
                if total_ep_actions > 0
                else 0
            )

            avg_ep_num_steps = (
                sum(
                    m["num_steps_in_episode"]
                    for m in self.completed_episode_metrics_buffer
                )
                / num_episodes_in_buffer
            )

            ep_wins = sum(
                1
                for m in self.completed_episode_metrics_buffer
                if m["game_outcome"] == 1
            )
            ep_losses = sum(
                1
                for m in self.completed_episode_metrics_buffer
                if m["game_outcome"] == -1
            )
            ep_draws = sum(
                1
                for m in self.completed_episode_metrics_buffer
                if m["game_outcome"] == 0
            )

            ep_win_rate = (
                ep_wins / num_episodes_in_buffer if num_episodes_in_buffer > 0 else 0
            )
            ep_loss_rate = (
                ep_losses / num_episodes_in_buffer if num_episodes_in_buffer > 0 else 0
            )
            ep_draw_rate = (
                ep_draws / num_episodes_in_buffer if num_episodes_in_buffer > 0 else 0
            )

            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/avg_episode_env_reward"
            ] = avg_ep_env_reward
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/avg_episode_action_accuracy"
            ] = avg_ep_action_accuracy
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/avg_episode_num_steps"
            ] = avg_ep_num_steps
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/episode_win_rate"
            ] = ep_win_rate
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/episode_loss_rate"
            ] = ep_loss_rate
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/episode_draw_rate"
            ] = ep_draw_rate
            wandb_metrics[
                f"{self.wandb_prepend or 'blackjack'}_train/num_episodes_in_log_period"
            ] = num_episodes_in_buffer

            logger.info(
                f"Logging metrics for {num_episodes_in_buffer} completed training episodes."
            )
            self.completed_episode_metrics_buffer = []
        await super().wandb_log(wandb_metrics)

    @classmethod
    def config_init(cls) -> Tuple[BlackjackEnvConfig, List[OpenaiConfig]]:
        env_config = BlackjackEnvConfig(
            # Fields from fundamental_prediction_environment.py's BaseEnvConfig init:
            tokenizer_name="NousResearch/DeepHermes-3-Llama-3-8B-Preview",
            group_size=16, # From Base, as not in BJ no_mc config's direct definition
            use_wandb=True,
            max_num_workers=128,
            rollout_server_url="http://localhost:8000",
            total_steps=2000,
            batch_size=1024, # Matches BlackjackEnvConfig (no_mc) default as well
            steps_per_eval=20,
            max_token_length=1024 * 16,
            inference_weight=1.0,
            wandb_name="fundamental_metric_prediction", # Strict: Use value from fundamental_prediction
            data_path_to_save_groups=None,
            eval_handling=EvalHandlingEnum.LIMIT_TRAIN,
            eval_limit_ratio=0.1,

            # BlackjackEnvConfig (no_mc version) specific fields (those NOT in BaseEnvConfig from fundamental_prediction)
            # using their defined defaults from BlackjackEnvConfig (no_mc):
            env_name="Blackjack-v1",        # Default from BlackjackEnvConfig (no_mc)
            temperature=0.7,            # Default from BlackjackEnvConfig (no_mc)
            top_p=0.9,                  # Default from BlackjackEnvConfig (no_mc)
            max_turns=5,                # Default from BlackjackEnvConfig (no_mc)
            thinking_active=True,       # Default from BlackjackEnvConfig (no_mc)
            eval_episodes=100,          # Default from BlackjackEnvConfig (no_mc)
            max_think_chars_history=3000, # Default from BlackjackEnvConfig (no_mc)
            max_trajectory_tokens=24576,# Default from BlackjackEnvConfig (no_mc)
            debug_mode=False,           # Default from BlackjackEnvConfig (no_mc)
        )
        server_configs = [
            OpenaiConfig(
                model_name="NousResearch/DeepHermes-3-Llama-3-8B-Preview",
                base_url="http://localhost:9004/v1",
                api_key="x",
                num_requests_for_eval=256, # From fundamental_prediction_environment.py
            )
        ]
        return env_config, server_configs

    @classmethod
    def cli(cls):
        super(BlackjackEnv, cls).cli()

    def _truncate_thinking_for_history(
        self, response_text: str, max_chars_fallback: int
    ) -> str:
        """Helper to truncate the <think> block of a response for message history."""
        try:
            think_start_tag = "<think>"
            think_end_tag = "</think>"

            think_start_idx = response_text.find(think_start_tag)
            think_end_idx = response_text.find(think_end_tag)

            if (
                think_start_idx != -1
                and think_end_idx != -1
                and think_start_idx < think_end_idx
            ):
                part_before_content = response_text[
                    : think_start_idx + len(think_start_tag)
                ]
                original_think_content = response_text[
                    think_start_idx + len(think_start_tag) : think_end_idx
                ].strip()
                part_after_content = response_text[think_end_idx:]

                truncated_think_content = original_think_content
                is_truncated = False

                if not original_think_content:
                    return response_text

                paragraphs = [
                    p.strip() for p in original_think_content.split("\n\n") if p.strip()
                ]
                if len(paragraphs) > 0:
                    last_paragraph = paragraphs[-1]
                    if len(last_paragraph) < len(original_think_content):
                        truncated_think_content = last_paragraph
                        is_truncated = True
                    elif len(original_think_content) > max_chars_fallback:
                        truncated_think_content = original_think_content[
                            -max_chars_fallback:
                        ]
                        is_truncated = True
                elif len(original_think_content) > max_chars_fallback:
                    truncated_think_content = original_think_content[
                        -max_chars_fallback:
                    ]
                    is_truncated = True

                if is_truncated and truncated_think_content:
                    if not truncated_think_content.startswith("... "):
                        truncated_think_content = (
                            "... " + truncated_think_content.lstrip()
                        )

                if (
                    not truncated_think_content.strip()
                    or truncated_think_content.strip() == "..."
                ):
                    final_content_for_block = ""
                else:
                    final_content_for_block = f"\n{truncated_think_content.strip()}\n"

                return f"{part_before_content.rstrip()}{final_content_for_block}{part_after_content.lstrip()}"

            return response_text
        except Exception as e:
            logger.error(
                f"Error in _truncate_thinking_for_history for text '{response_text[:200]}...': {e}",
                exc_info=True,
            )
            return response_text

    def _ensure_trajectory_token_limit(
        self, trajectory: List[BlackjackScoredDataGroup]
    ) -> List[BlackjackScoredDataGroup]:
        """
        Ensure token sequences in a trajectory don't exceed max_trajectory_tokens.
        Attempts to uniformly truncate older messages (preferably paired turns) from all alternatives within a step.
        The system prompt, last environment observation, and last agent response are preserved as a minimum.
        If a step still exceeds the limit after maximum possible truncation, it is discarded.

        Args:
            trajectory: List of BlackjackScoredDataGroup from an episode

        Returns:
            The trajectory with potentially truncated messages/tokens/masks or filtered steps
        """
        if not trajectory:
            return trajectory

        filtered_trajectory: List[BlackjackScoredDataGroup] = []

        for step_idx, original_step_data in enumerate(trajectory):
            if not (
                original_step_data.get("messages")
                and original_step_data.get("tokens")
                and original_step_data.get("masks")
                and original_step_data.get("seed") is not None # seed is mandatory for new group
            ):
                logger.warning(
                    f"[_ensure_trajectory_token_limit] Step {step_idx} "
                    f"is missing critical data (messages, tokens, masks, or seed). Skipping."
                )
                continue

            # Initial token calculation from original data to see if truncation is needed
            # Ensure tokens are lists of integers before calling len
            max_initial_tokens = 0
            if original_step_data["tokens"]:
                 max_initial_tokens = max(
                    len(alt_tokens) for alt_tokens in original_step_data["tokens"] if isinstance(alt_tokens, list)
                 ) if any(isinstance(alt_tokens, list) for alt_tokens in original_step_data["tokens"]) else 0

            if max_initial_tokens <= self.config.max_trajectory_tokens:
                filtered_trajectory.append(original_step_data)
                logger.info(
                    f"[_ensure_trajectory_token_limit] Step {step_idx} compliant. "
                    f"Max tokens: {max_initial_tokens}"
                )
                continue

            logger.info(
                f"[_ensure_trajectory_token_limit] Step {step_idx} (max tokens: {max_initial_tokens}) "
                f"exceeds limit ({self.config.max_trajectory_tokens}). Attempting truncation."
            )

            # Prepare working copies for modification
            # Ensure deep copies for lists of dicts if dicts are modified, but here we pop from list of dicts.
            working_messages = [msgs_list.copy() for msgs_list in original_step_data["messages"] or []]
            working_tokens = [tkns_list.copy() for tkns_list in original_step_data["tokens"] or []]
            working_masks = [msks_list.copy() for msks_list in original_step_data["masks"] or []]
            max_current_tokens = max_initial_tokens
            num_alternatives = len(working_messages)

            if num_alternatives == 0: # Should not happen if initial checks passed
                logger.warning(f"[_ensure_trajectory_token_limit] Step {step_idx} has no alternatives after copying. Skipping.")
                continue

            retokenization_error_this_step = False
            while max_current_tokens > self.config.max_trajectory_tokens:
                target_pop_counts_per_alt = []
                for alt_idx in range(num_alternatives):
                    alt_msg_list = working_messages[alt_idx]

                    # Calculate how many initial messages (after system prompt) can be popped.
                    # Preserving: system prompt (index 0), last agent response, and its preceding env observation.
                    num_preserved_at_end = 0
                    if len(alt_msg_list) > 1 and alt_msg_list[-1]["role"] in UNMASKED_ROLES:
                        num_preserved_at_end = 1  # Last agent response
                        if len(alt_msg_list) > 2 and alt_msg_list[-2]["role"] == "environment":
                            num_preserved_at_end = 2 # Agent response + preceding env observation
                    
                    # Number of messages available for popping (between system prompt and preserved end messages)
                    # Subtract 1 for the system prompt itself (which is never popped from index 0).
                    available_to_pop = len(alt_msg_list) - 1 - num_preserved_at_end

                    if available_to_pop <= 0:
                        target_pop_counts_per_alt.append(0)
                    else:
                        # Try to pop a pair (environment, agent) if they are at list[1] and list[2]
                        can_pop_pair = (
                            available_to_pop >= 2 and
                            len(alt_msg_list) > 2 and # Ensure messages at index 1 and 2 exist
                            alt_msg_list[1]["role"] == "environment" and
                            alt_msg_list[2]["role"] in UNMASKED_ROLES
                        )
                        if can_pop_pair:
                            target_pop_counts_per_alt.append(2)
                        else: # Can pop at least 1 since available_to_pop > 0
                            target_pop_counts_per_alt.append(1)
                
                positive_pop_counts = [c for c in target_pop_counts_per_alt if c > 0]
                if not positive_pop_counts:
                    break # No alternative can be truncated further
                
                min_pop_this_round = min(positive_pop_counts)

                # Pop messages and re-tokenize
                temp_new_alt_tokens = []
                temp_new_alt_masks = []
                max_tokens_after_this_trunc = 0

                for alt_idx in range(num_alternatives):
                    for _ in range(min_pop_this_round):
                        if len(working_messages[alt_idx]) > 1: # Ensure there's something to pop after system
                            working_messages[alt_idx].pop(1)
                        else:
                            logger.error(
                                f"[_ensure_trajectory_token_limit] Critical error during pop for "
                                f"alt {alt_idx}, step {step_idx}. List too short."
                            )
                            retokenization_error_this_step = True; break
                    if retokenization_error_this_step: break
                    
                    try:
                        tokenized_alt = tokenize_for_trainer(self.tokenizer, working_messages[alt_idx])
                        temp_new_alt_tokens.append(tokenized_alt["tokens"])
                        temp_new_alt_masks.append(tokenized_alt["masks"])
                        max_tokens_after_this_trunc = max(max_tokens_after_this_trunc, len(tokenized_alt["tokens"]))
                    except Exception as e:
                        logger.error(
                            f"[_ensure_trajectory_token_limit] Error re-tokenizing alt {alt_idx} "
                            f"in step {step_idx} after truncation: {e}"
                        )
                        retokenization_error_this_step = True; break
                
                if retokenization_error_this_step: break

                working_tokens = temp_new_alt_tokens
                working_masks = temp_new_alt_masks
                max_current_tokens = max_tokens_after_this_trunc
                logger.debug(
                    f"[_ensure_trajectory_token_limit] Step {step_idx}, after uniform pop of {min_pop_this_round}, "
                    f"max tokens: {max_current_tokens}"
                )
            # End of while loop for truncation attempts

            if not retokenization_error_this_step and max_current_tokens <= self.config.max_trajectory_tokens:
                updated_step_data: BlackjackScoredDataGroup = {
                    "seed": original_step_data["seed"],
                    "messages": working_messages,
                    "tokens": working_tokens,
                    "masks": working_masks,
                    "scores": original_step_data.get("scores"),
                    "parsed_action": original_step_data.get("parsed_action")
                }
                filtered_trajectory.append(updated_step_data)
                logger.info(
                    f"[_ensure_trajectory_token_limit] Step {step_idx} successfully processed. "
                    f"Final max tokens: {max_current_tokens}"
                )
            else:
                logger.warning(
                    f"[_ensure_trajectory_token_limit] Discarding step {step_idx}. "
                    f"Max tokens ({max_current_tokens}) still exceed limit ({self.config.max_trajectory_tokens}) "
                    f"or retokenization error occurred ({retokenization_error_this_step})."
                )

        if len(filtered_trajectory) < len(trajectory):
            logger.warning(
                f"[_ensure_trajectory_token_limit] Filtered out "
                f"{len(trajectory) - len(filtered_trajectory)} steps "
                f"due to token limit constraints. Original: {len(trajectory)}, Filtered: {len(filtered_trajectory)}"
            )
        return filtered_trajectory


if __name__ == "__main__":
    BlackjackEnv.cli()
