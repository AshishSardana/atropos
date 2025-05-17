# OpenMathReasoning Environment

This document provides guidelines for using and testing the OpenMathReasoning environment in Atropos.

## Overview

The OpenMathReasoning environment is designed to train models on advanced mathematical problems that require formal mathematical notation and sophisticated reasoning. It uses the NVIDIA OpenMathReasoning dataset, which contains complex mathematical problems with LaTeX formatted answers.

## Dataset

The environment uses the [NVIDIA OpenMathReasoning dataset](https://huggingface.co/datasets/nvidia/OpenMathReasoning), which contains advanced mathematical problems covering various topics like algebra, calculus, number theory, geometry, and more.

Dataset structure:
- `problem`: The mathematical problem statement
- `expected_answer`: The expected solution in LaTeX format
- `problem_type`: Type of mathematical problem (optional)
- `problem_source`: Source of the problem (optional)
- `generation_model`: The model that generated the problem (if applicable)
- `pass_rate_72b_tir`: Pass rate of this problem with the 72B TIR model

## Configuration

The environment can be configured using the following YAML file:

```yaml
# OpenMathReasoning Environment configuration
env:
  group_size: 8
  max_batches_offpolicy: 3
  tokenizer_name: "NousResearch/DeepHermes-3-Llama-3-3B-Preview"
  use_wandb: true
  rollout_server_url: "http://localhost:8000"
  wandb_name: "open_math_reasoning"
  ensure_scores_are_not_same: true
  data_path_to_save_groups: null
  include_messages: true
  max_token_length: 2048
  batch_size: 12
  total_steps: 1000
  steps_per_eval: 100

# OpenAI server configurations
openai:
  - model_name: "NousResearch/DeepHermes-3-Llama-3-3B-Preview"
    base_url: "http://localhost:9001/v1"
    api_key: "x"
    num_requests_for_eval: 256
    weight: 1.0

slurm: false
testing: false
```

## System Prompt

The environment uses a specialized system prompt to guide the model in solving mathematical problems:

```
You are a deep thinking AI specializing in advanced mathematics. 
You may use extremely long chains of thought 
to deeply consider mathematical problems and deliberate with yourself via systematic 
reasoning processes to help come to a correct solution prior to answering. 
You should enclose your thoughts and internal monologue inside <think> </think> 
tags, and then provide your solution or response to the problem.

You are allocated a maximum of 2048 tokens, please strive to use less.

You should use proper LaTeX notation when writing mathematical expressions and formulas.
For example, use \frac{a}{b} for fractions, \sqrt{x} for square roots, and ^ for exponents.

You will then provide your final answer like this: \boxed{your answer here}
It is important that you provide your answer in the correct LaTeX format.
If you do not, you will not receive credit for your answer.
So please end your answer with \boxed{your answer here}
```

## LaTeX Format Considerations

When using this environment, it's important to understand that the mathematical expressions are in LaTeX format. The environment includes:

1. LaTeX extraction tools to parse responses
2. LaTeX normalization to handle equivalent expressions
3. Verification of mathematical equivalence

Common LaTeX notations you'll see in the dataset:
- `\frac{a}{b}` for fractions
- `\sqrt{x}` for square roots
- `^` for exponents (e.g., `x^2`)
- `\cdot` for multiplication
- `\sum`, `\prod`, `\int` for summation, product, and integral
- `\mathbb{R}`, `\mathbb{Z}` for special sets
- `\boxed{}` for the final answer

## Testing the Environment

### Using the Test Script

You can test the environment using the provided test script:

```bash
cd atropos
python -m scripts.test_open_math_reasoning
```

This script:
1. Loads a random problem from the OpenMathReasoning dataset
2. Runs it through the system with the prescribed prompt
3. Saves the result and analyzes the response format

### Running the Environment Directly

To run the environment directly:

```bash
cd atropos
python -m environments.open_math_reasoning_server process
```

### Analyzing LaTeX Formats

To analyze LaTeX formats in the dataset:

```bash
cd atropos
python -m scripts.analyze_latex_format
```

This will generate a report on common LaTeX patterns and commands in the dataset, which can be used to fine-tune the system prompt.

## Scoring Mechanism

The environment scores responses as follows:

1. The expected answer and model response are parsed using LaTeX extraction tools
2. Mathematical equivalence is verified using the `verify` function
3. Responses are rewarded (score = 1.0) if they are mathematically equivalent to the expected answer
4. Length penalties are applied when responses are much longer than necessary

## Troubleshooting

### Common Issues

1. **LaTeX Parsing Failures**: If the model output contains malformed LaTeX, it may fail to parse correctly. 
   - Solution: Modify the system prompt to provide clearer LaTeX formatting instructions.

2. **Missing Thinking Section**: If the model doesn't use the `<think>` tags properly.
   - Solution: Emphasize the importance of these tags in the system prompt.

3. **Equivalent but Different Answers**: Sometimes mathematically equivalent answers might be formatted differently.
   - Solution: The environment uses normalization to handle this, but you can adjust the normalization config if needed.

## Example Problem and Solution

**Problem:**
```
Given a group of N balls consisting of C colors, where the number of balls in each color is represented as n_1, n_2, \ldots, n_C (with n_1 + n_2 + \ldots + n_C = N), what is the probability that when A balls are randomly picked (where A \le N), the picked balls consist of a_1, a_2, \ldots, a_C balls of each color, where a_1 + a_2 + \ldots + a_C = A?
```

**Expected Answer:**
```
\(\frac{C_{n_1}^{a_1} \cdot C_{n_2}^{a_2} \cdots C_{n_C}^{a_C}}{C_{N}^{A}}\)
```

**Example Solution Process (What the model should produce):**
```
<think>
Let's approach this step by step. We're looking for a probability, which is the number of favorable outcomes divided by the total number of possible outcomes.

Total number of possible outcomes: This is the number of ways to choose A balls from N balls, which is C(N,A) or N choose A.

Favorable outcomes: We need to pick exactly a_1 balls of color 1, a_2 balls of color 2, ..., and a_C balls of color C.
- For color 1: We need to choose a_1 balls from n_1 balls. This can be done in C(n_1, a_1) ways.
- For color 2: We need to choose a_2 balls from n_2 balls. This can be done in C(n_2, a_2) ways.
- ...
- For color C: We need to choose a_C balls from n_C balls. This can be done in C(n_C, a_C) ways.

Since these selections are independent, we multiply the number of ways for each color:
C(n_1, a_1) × C(n_2, a_2) × ... × C(n_C, a_C)

So the probability is:
P = [C(n_1, a_1) × C(n_2, a_2) × ... × C(n_C, a_C)] / C(N, A)
</think>

To find the probability that when A balls are randomly picked from N balls of C colors, the picked balls consist of a_1, a_2, ..., a_C balls of each color:

I need to find the ratio of favorable outcomes to total possible outcomes.

**Total possible outcomes:** The number of ways to select A balls from N balls, which is C(N, A) or \binom{N}{A}.

**Favorable outcomes:** I need to select exactly a_1 balls of color 1, a_2 balls of color 2, and so on. For each color:
- From n_1 balls of color 1, I need to select a_1 balls: \binom{n_1}{a_1} ways
- From n_2 balls of color 2, I need to select a_2 balls: \binom{n_2}{a_2} ways
- And so on for all C colors

Since these selections are independent, I multiply them together:
\binom{n_1}{a_1} \cdot \binom{n_2}{a_2} \cdot ... \cdot \binom{n_C}{a_C}

Therefore, the probability is:

\boxed{\frac{\binom{n_1}{a_1} \cdot \binom{n_2}{a_2} \cdot ... \cdot \binom{n_C}{a_C}}{\binom{N}{A}}}
```

## Feedback and Improvements

After using the environment, consider:

1. Analyzing the LaTeX formats in the real responses using the analyze script
2. Updating the system prompt based on common errors or patterns
3. Adjusting the verification logic if needed
4. Fine-tuning the length penalty parameters for your specific training goals

## Dependencies

The environment requires:
- `datasets` for loading the OpenMathReasoning dataset
- `latex2sympy2_extended` for LaTeX normalization
- `math_verify` for mathematical verification
- Standard Atropos dependencies 