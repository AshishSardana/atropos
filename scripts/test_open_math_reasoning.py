#!/usr/bin/env python3
"""
Script to test the OpenMathReasoning environment with a sample problem.

This script loads the OpenMathReasoning dataset, selects a sample problem,
and runs it through the environment to verify the implementation.
"""

import asyncio
import random
import json
import os
from typing import Dict, List, Any, Tuple

from datasets import load_dataset
from pprint import pprint

from atroposlib.envs.base import BaseEnvConfig, APIServerConfig
from atroposlib.server import OpenAIServer
from environments.open_math_reasoning_server import OpenMathReasoningEnv, system_prompt

async def main():
    # Create output directory if it doesn't exist
    os.makedirs("output", exist_ok=True)
    output_file = "output/test_results.json"
    
    # Load the dataset
    print("Loading OpenMathReasoning dataset...")
    dataset = load_dataset("nvidia/OpenMathReasoning", split="cot")
    
    # Select a random sample
    sample_index = random.randint(0, len(dataset) - 1)
    sample = dataset[sample_index]
    
    print(f"\nSelected sample problem #{sample_index}:")
    print(f"Problem: {sample['problem']}")
    print(f"Expected answer: {sample['expected_answer']}")
    if sample.get('problem_source'):
        print(f"Source: {sample['problem_source']}")
    
    # Setup configuration
    config = BaseEnvConfig(
        tokenizer_name="NousResearch/DeepHermes-3-Llama-3-3B-Preview",
        group_size=1,  # Just testing with one response
        use_wandb=False,
        rollout_server_url="http://localhost:8000",
        total_steps=1,
        batch_size=1,
        steps_per_eval=1,
        max_token_length=2048,
        wandb_name="open_math_reasoning_test",
    )
    
    server_config = APIServerConfig(
        model_name="NousResearch/DeepHermes-3-Llama-3-3B-Preview",
        base_url="http://localhost:9001/v1",
        api_key="x",
        num_requests_for_eval=1,
    )
    
    # Check if OpenAI API key is available (for real testing)
    openai_api_key = os.environ.get("OPENAI_API_KEY")
    if openai_api_key:
        print("\nUsing OpenAI API for testing...")
        # Override with OpenAI settings
        server_config = APIServerConfig(
            model_name="gpt-4o-mini",
            api_key=openai_api_key,
            num_requests_for_eval=1,
        )
    
    # Setup the server
    server = OpenAIServer(server_config)
    
    # Test direct completion (simplified test without full environment setup)
    print("\nTesting with direct completion...")
    try:
        completion = await server.chat_completion(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": sample["problem"]},
            ],
            n=1,
            max_tokens=config.max_token_length,
            temperature=0.0,
        )
        
        model_response = completion.choices[0].message.content
        
        # Save the results
        test_results = {
            "problem_index": sample_index,
            "problem": sample["problem"],
            "expected_answer": sample["expected_answer"],
            "model_response": model_response,
        }
        
        with open(output_file, "w") as f:
            json.dump(test_results, f, indent=2)
        
        print(f"\nTest results saved to {output_file}")
        
        # Print the model's response
        print("\nModel response:")
        print("-" * 80)
        print(model_response)
        print("-" * 80)
        
        # Check if the response contains a thinking section
        if "<think>" in model_response and "</think>" in model_response:
            print("\n✓ Response contains thinking section")
        else:
            print("\n✗ Response missing thinking section")
        
        # Check if the response contains LaTeX
        latex_commands = [
            r"\frac", r"\sqrt", r"\boxed", r"\cdot", r"\sum", r"\prod", 
            r"\int", r"\mathbb", r"\overline", r"\text"
        ]
        latex_found = any(cmd in model_response for cmd in latex_commands)
        if latex_found:
            print("✓ Response contains LaTeX notation")
        else:
            print("✗ Response may be missing LaTeX notation")
        
        # Check if the response ends with a boxed answer
        if r"\boxed{" in model_response:
            print("✓ Response contains boxed answer")
        else:
            print("✗ Response missing boxed answer")
            
    except Exception as e:
        print(f"Error running test: {e}")
    
    # Close the server
    await server.close()

if __name__ == "__main__":
    asyncio.run(main()) 