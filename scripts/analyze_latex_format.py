#!/usr/bin/env python3
"""
Script to analyze LaTeX formats in the OpenMathReasoning dataset.

This script examines the expected_answer field in the OpenMathReasoning dataset
to identify common LaTeX patterns and structures. The analysis results can be used
to improve the system prompt for the OpenMathReasoningEnv.
"""

import re
import json
from collections import Counter, defaultdict
from typing import Dict, List, Any, Set, Tuple
import os

from datasets import load_dataset
from tqdm import tqdm

# LaTeX commands/structures to look for
LATEX_PATTERNS = [
    (r"\\frac\{.*?\}\{.*?\}", "Fractions"),
    (r"\\sqrt\{.*?\}", "Square roots"),
    (r"\\boxed\{.*?\}", "Boxed expressions"),
    (r"\^", "Exponents"),
    (r"_", "Subscripts"),
    (r"\\cdot", "Multiplication dot"),
    (r"\\sum", "Summation"),
    (r"\\prod", "Product"),
    (r"\\int", "Integral"),
    (r"\\lim", "Limit"),
    (r"\\infty", "Infinity"),
    (r"\\mathbb\{.*?\}", "Special number sets"),
    (r"\\overline\{.*?\}", "Overline"),
    (r"\\text\{.*?\}", "Text in math mode"),
    (r"\\ldots", "Ellipsis"),
    (r"\\approx", "Approximation"),
    (r"\\neq", "Not equal"),
    (r"\\geq", "Greater than or equal"),
    (r"\\leq", "Less than or equal"),
    (r"\\rightarrow", "Right arrow"),
    (r"\\leftarrow", "Left arrow"),
    (r"\\leftrightarrow", "Bidirectional arrow"),
    (r"\\Rightarrow", "Implies"),
    (r"\\Leftarrow", "Is implied by"),
    (r"\\Leftrightarrow", "If and only if"),
    (r"\\forall", "For all"),
    (r"\\exists", "There exists"),
    (r"\\subset", "Subset"),
    (r"\\subseteq", "Subset or equal"),
    (r"\\cup", "Union"),
    (r"\\cap", "Intersection"),
    (r"\\emptyset", "Empty set"),
    (r"\\in", "Element of"),
    (r"\\notin", "Not element of"),
    (r"\\sin", "Sine function"),
    (r"\\cos", "Cosine function"),
    (r"\\tan", "Tangent function"),
    (r"\\log", "Logarithm"),
    (r"\\ln", "Natural logarithm"),
    (r"\\exp", "Exponential function"),
]

def main():
    # Create output directory if it doesn't exist
    os.makedirs("output", exist_ok=True)
    output_file = "output/latex_analysis.json"
    
    print(f"Loading OpenMathReasoning dataset...")
    dataset = load_dataset("nvidia/OpenMathReasoning", split="cot")
    
    # Extract expected_answer field
    answers = [item["expected_answer"] for item in dataset]
    
    print(f"Analyzing {len(answers)} answers...")
    
    # Initialize counters and collections
    pattern_counts = Counter()
    latex_commands = Counter()
    answer_structures = Counter()
    complex_structures = []
    
    # Track unique LaTeX commands
    all_commands = set()
    
    # Track escaping patterns
    escape_patterns = Counter()
    
    # Analyze answers
    for answer in tqdm(answers):
        # Check for LaTeX patterns
        for pattern, name in LATEX_PATTERNS:
            if re.search(pattern, answer):
                pattern_counts[name] += 1
        
        # Extract all LaTeX commands
        commands = re.findall(r"\\[a-zA-Z]+", answer)
        for cmd in commands:
            latex_commands[cmd] += 1
            all_commands.add(cmd)
        
        # Check for overall structure
        if answer.startswith("\\boxed{") and answer.endswith("}"):
            answer_structures["Boxed only"] += 1
        elif answer.startswith("\\(") and answer.endswith("\\)"):
            answer_structures["Math delimiters \\(\\)"] += 1
        elif answer.startswith("$") and answer.endswith("$"):
            answer_structures["Math delimiters $"] += 1
        elif answer.startswith("$$") and answer.endswith("$$"):
            answer_structures["Display math $$"] += 1
        
        # Check for escaped special characters
        escape_matches = re.findall(r"\\[\{\}\[\]\(\)]", answer)
        for escape in escape_matches:
            escape_patterns[escape] += 1
        
        # Save complex examples (many LaTeX commands)
        if len(commands) > 10:
            complex_structures.append({
                "answer": answer,
                "command_count": len(commands),
                "unique_commands": list(set(commands))
            })
    
    # Sort complex examples by command count (most complex first)
    complex_structures.sort(key=lambda x: x["command_count"], reverse=True)
    
    # Prepare results
    results = {
        "total_answers": len(answers),
        "pattern_counts": {k: v for k, v in pattern_counts.most_common()},
        "latex_commands": {k: v for k, v in latex_commands.most_common(30)},  # Top 30 commands
        "all_unique_commands": sorted(list(all_commands)),
        "answer_structures": {k: v for k, v in answer_structures.most_common()},
        "escape_patterns": {k: v for k, v in escape_patterns.most_common()},
        "complex_examples": complex_structures[:10],  # Top 10 most complex examples
    }
    
    # Save results
    with open(output_file, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"Analysis results saved to {output_file}")
    
    # Print summary
    print("\nSummary:")
    print(f"Total answers analyzed: {len(answers)}")
    print("\nTop 10 LaTeX patterns:")
    for pattern, count in pattern_counts.most_common(10):
        print(f"  {pattern}: {count} ({count/len(answers)*100:.1f}%)")
    
    print("\nTop 10 LaTeX commands:")
    for cmd, count in latex_commands.most_common(10):
        print(f"  {cmd}: {count} ({count/len(answers)*100:.1f}%)")
    
    print("\nAnswer structures:")
    for structure, count in answer_structures.most_common():
        print(f"  {structure}: {count} ({count/len(answers)*100:.1f}%)")
    
    # Generate system prompt suggestions
    print("\nSuggested system prompt additions:")
    top_patterns = [name for name, _ in pattern_counts.most_common(10)]
    top_commands = [cmd for cmd, _ in latex_commands.most_common(10)]
    
    prompt_suggestion = "Based on the analysis, consider adding these instructions to the system prompt:\n\n"
    prompt_suggestion += "You should use proper LaTeX notation when writing mathematical expressions. "
    prompt_suggestion += f"Common notations in this domain include: {', '.join(top_patterns)}.\n\n"
    prompt_suggestion += "Make sure to use these common LaTeX commands correctly: "
    prompt_suggestion += ", ".join(top_commands)
    
    print(prompt_suggestion)

if __name__ == "__main__":
    main() 