# OpenMathReasoning Environment Specification

## Overview
This specification outlines the modifications needed to adapt the existing GSM8k environment in Atropos to work with the OpenMathReasoning dataset. The goal is to maintain the same training methodologies and techniques while accommodating the different dataset structure and mathematical complexity.

## Dataset Comparison

**GSM8k Sample:**
```json
{
    "question": "Natalia sold clips to 48 of her friends in April, and then she sold half as many clips in May. How many clips did Natalia sell altogether in April and May?",
    "answer": "Natalia sold 48/2 = <<48/2=24>>24 clips in May.\nNatalia sold 48+24 = <<48+24=72>>72 clips altogether in April and May.\n#### 72"
}
```

**OpenMathReasoning Sample:**
```json
{
  "expected_answer": "\\(\\frac{C_{n_1}^{a_1} \\cdot C_{n_2}^{a_2} \\cdots C_{n_C}^{a_C}}{C_{N}^{A}}\\)",
  "problem_type": null,
  "problem_source": "aops_c6_high_school_olympiads",
  "generation_model": "DeepSeek-R1",
  "pass_rate_72b_tir": 0.65625,
  "problem": "Given a group of N balls consisting of C colors, where the number of balls in each color is represented as n_1, n_2, \\ldots, n_C (with n_1 + n_2 + \\ldots + n_C = N), what is the probability that when A balls are randomly picked (where A \\le N), the picked balls consist of a_1, a_2, \\ldots, a_C balls of each color, where a_1 + a_2 + \\ldots + a_C = A?",
  "generated_solution": "<think> Okay, so I need to find the probability that when I pick A balls out of N, where there are C different colors, the number of each color I pick is exactly a1, a2, ..., aC. Hmm, let's think about how to approach this. First, probability problems often involve combinations. </think>\\n\\nTo find the probability that when A balls are randomly picked from N balls consisting of C colors, where the number of balls in each color is represented as n1, n2, ..., nC (with n1 + n2 + ... + nC = N), the picked balls consist of a1, a2, ..., aC balls of each color (where a1 + a2 + ... + aC = A), we can use the multivariate hypergeometric distribution.\\n\\nThe probability P is the ratio of the number of favorable to total outcomes: P = [C(n1, a1) * C(n2, a2) * ... * C(nC, aC)] / C(N, A).\\n\\nFinal solution: P = (\\u220f_{i=1}^{C} C(n_i, a_i)) / C(N, A).",
  "inference_mode": "cot",
  "has_answer_extracted": true
}
```

## Plan

1. **Fork the GSM8k Environment**: Create a new environment based on the existing GSM8k implementation
2. **Load OpenMathReasoning Dataset**: Modify dataset loading and field mappings
3. **Update System Prompt**: Adjust to accommodate advanced mathematical notation and problems
4. **Enhance Reward Function**: Modify for LaTeX evaluation and mathematical equivalence
5. **Test Implementation**: Verify correct functioning with sample problems
6. **Configure Training Parameters**: Set up appropriate training parameters
7. **Deploy and Monitor**: Launch environment and track training progress

## Tasks and Subtasks

### 1. Environment Setup
- [ ] Create a new Python file `open_math_reasoning_server.py` based on `gsm8k_server.py`
- [ ] Update imports and class name to `OpenMathReasoningEnv`
- [ ] Modify class documentation and docstrings

### 2. Dataset Configuration
- [ ] Add OpenMathReasoning dataset path/name in config_init
- [ ] Map field names: 
  - [ ] `problem` → input field (was `question`)
  - [ ] `expected_answer` → answer field (was `answer`)
- [ ] Determine how to handle extra fields like `problem_source`, `generation_model`
- [ ] Configure appropriate train/test split ratio

### 3. System Prompt Modification
- [ ] Retain the thinking structure with `<think>` tags
- [ ] Add instructions for handling mathematical notation
- [ ] Include guidance for LaTeX formatting in answers
- [ ] Specify answer format requirements (e.g., placing final answer in \boxed{})
- [ ] Update max token allocation instructions

### 4. Reward Function Enhancement
- [ ] Modify `score()` method to handle LaTeX mathematical equivalence
- [ ] Implement or adapt a LaTeX parser for answer extraction
- [ ] Create appropriate normalizing functions for comparing mathematical expressions
- [ ] Support various mathematical notations and equivalent expressions
- [ ] Add appropriate length penalty calculation

### 5. Interface & Parsing Implementation
- [ ] Update `get_next_item()` to handle the OpenMathReasoning format
- [ ] Adjust parsing in `collect_trajectories()` for math notation responses
- [ ] Modify answer extraction logic to handle mathematical notation
- [ ] Ensure proper handling of Unicode and LaTeX characters

### 6. Metrics & Evaluation
- [ ] Define appropriate evaluation metrics for math problems
- [ ] Add reporting for advanced math problem categories if applicable
- [ ] Configure WandB integration for specialized math visualizations
- [ ] Set up appropriate batching and evaluation frequency

### 7. Documentation & Configuration
- [ ] Create YAML configuration file for the environment
- [ ] Document environment capabilities and limitations
- [ ] Add examples of expected input/output
- [ ] Document any special requirements for mathematical notation

## Closing the Loop (Validating the Solution)

### Verification Steps
1. **Unit Testing**:
   - Test dataset loading with sample OpenMathReasoning problems
   - Verify field mappings work correctly
   - Test answer extraction from various response formats
   - Validate LaTeX parsing and normalization

2. **Integration Testing**:
   - Run environment in `process` mode to generate sample outputs
   - Verify reward calculation on known problems and solutions
   - Test with different models to ensure consistent behavior

3. **Validation Metrics**:
   - Track accuracy on a held-out validation set
   - Compare performance to published benchmarks for the dataset
   - Validate that thinking patterns are correctly rewarded
   - Ensure the environment can distinguish between correct and incorrect mathematical solutions

4. **Training Loop Validation**:
   - Run a small-scale training loop with a lightweight model
   - Verify that gradients flow correctly
   - Confirm improvements in performance over baseline
   - Check that the model learns to format answers correctly

5. **Final Deployment Checklist**:
   - Documentation complete and accurate
   - All tests passing
   - Environment parameters optimized
   - Model can be trained successfully
   - Results can be reproduced consistently

### Success Criteria
- Environment correctly loads and processes OpenMathReasoning dataset
- System successfully rewards correct mathematical reasoning and answers
- Training shows improvement in model performance over time
- Final model achieves comparable or better pass rates than original benchmark
- Environment integrates seamlessly with the Atropos framework

### Fallback Plan
If the standard reward functions cannot handle complex mathematical equivalence, consider:
1. Using a specialized mathematical expression comparison library
2. Implementing a simplified scoring mechanism for prototype testing
3. Leveraging an external tool or API for LaTeX validation 