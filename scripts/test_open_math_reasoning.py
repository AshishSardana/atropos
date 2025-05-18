#!/usr/bin/env python3
"""
Script to test the OpenMathReasoning environment with a sample problem.

This script loads the OpenMathReasoning dataset, selects a sample problem,
and runs it through the environment to verify the implementation.
"""

import random
import json
import os
import http.client
from typing import Dict

from datasets import load_dataset
from pprint import pprint

# Define the system prompt directly to avoid importing dependencies that require torch
system_prompt = (
    "You are a deep thinking AI specializing in advanced mathematics. "
    "You may use extremely long chains of thought "
    "to deeply consider mathematical problems and deliberate with yourself via systematic "
    "reasoning processes to help come to a correct solution prior to answering. "
    "You should enclose your thoughts and internal monologue inside <think> </think> "
    "tags, and then provide your solution or response to the problem.\n\n"
)

system_prompt += """You are allocated a maximum of 2048 tokens, please strive to use less.

You should use proper LaTeX notation when writing mathematical expressions and formulas.
For example, use \\frac{a}{b} for fractions, \\sqrt{x} for square roots, and ^ for exponents.

You will then provide your final answer like this: \\boxed{your answer here}
It is important that you provide your answer in the correct LaTeX format.
If you do not, you will not receive credit for your answer.
So please end your answer with \\boxed{your answer here}"""

def main():
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
    
    # Get API key from environment variables
    api_key = os.environ.get("NOUS_API_KEY")
    if not api_key:
        print("Error: NOUS_API_KEY environment variable not found.")
        print("Please add it to your .env file in the atropos directory:")
        print("NOUS_API_KEY=your_api_key_here")
        return  # Exit the function if API key is missing

    
    # Setup NousResearch API
    print("\nUsing NousResearch API for testing...")
    conn = http.client.HTTPSConnection("inference-api.nousresearch.com")
    headers = {
        'Authorization': f"Bearer {api_key}",
        'Content-Type': "application/json"
    }
    
    # Define max tokens
    max_token_length = 16384
    
    # Test with NousResearch API
    print("\nTesting with NousResearch API...")
    try:
        # Prepare payload
        payload = {
            "model": "DeepHermes-3-Mistral-24B-Preview",
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt
                },
                {
                    "role": "user",
                    "content": sample["problem"]
                }
            ],
            "max_tokens": max_token_length
        }
        
        # Send request
        conn.request("POST", "/v1/chat/completions", json.dumps(payload), headers)
        res = conn.getresponse()
        data = res.read()
        
        # Parse response
        response_data = json.loads(data.decode("utf-8"))
        
        # Print full response for debugging
        print("\nAPI Response:")
        print(json.dumps(response_data, indent=2))
        
        # Check if response contains an error
        if "error" in response_data:
            print(f"\nAPI Error: {response_data.get('error', {}).get('message', 'Unknown error')}")
            return
            
        # Extract model response
        if "choices" in response_data and len(response_data["choices"]) > 0:
            model_response = response_data["choices"][0]["message"]["content"]
            
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
        else:
            print("Error: API response doesn't contain expected 'choices' field")
            print("Response structure:", list(response_data.keys()))
            
    except Exception as e:
        print(f"Error running test: {e}")
        print("Raw response data:", data.decode("utf-8") if 'data' in locals() else "No data received")

if __name__ == "__main__":
    main() 