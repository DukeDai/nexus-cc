#!/usr/bin/env python3
"""
End-to-end integration test for RalphLoop closed-loop execution.

This script validates that:
1. LLM generates a write_file tool call
2. Tool is executed (file is written)
3. LLM is called again with result
4. LLM confirms success
5. File exists and contains valid Python
"""

import os
import sys
import subprocess

# Ensure we can import from src
sys.path.insert(0, '/Users/dukedai/Dev/nexus')

from nexus_core import LLMClient, ToolExecutor, TOOL_DEFINITIONS  # noqa: F401


def run_loop():
    """Run the closed-loop test."""
    
    print("=" * 70)
    print("RALPHLOOP CLOSED-LOOP INTEGRATION TEST")
    print("=" * 70)
    
    # Initialize the LLM client and tool executor
    llm = LLMClient(provider="auto")
    executor = ToolExecutor(workdir=None)
    
    print(f"\n[INFO] LLM Provider: {llm.provider}")
    print(f"[INFO] Model: /Users/dukedai/Dev/nexus/src/llm/model_router.py or auto-detected")
    print(f"[INFO] Tool Executor workdir: {executor.workdir}")
    
    # The task for the agent
    task = """Write a simple Python function that adds two numbers, save it to /tmp/test_add.py, and verify it works.
    
Specifically:
1. Create a file /tmp/test_add.py with a function called `add` that takes two numbers and returns their sum
2. The file should be executable (have proper Python syntax)
3. Use the bash tool to run: python /tmp/test_add.py to verify it works

Do NOT write any tests or docstrings - just the bare function."""
    
    print(f"\n[TASK]\n{task}\n")
    
    # Build the conversation messages
    messages = [
        {"role": "user", "content": task}
    ]
    
    # Run the loop: LLM → tool_calls → execute → repeat
    max_iterations = 5
    iteration = 0
    
    print("\n" + "-" * 70)
    print("LOOP ITERATIONS")
    print("-" * 70)
    
    while iteration < max_iterations:
        iteration += 1
        print(f"\n>>> Iteration {iteration} <<<")
        print(f"Messages in history: {len(messages)}")
        
        # Call LLM
        print("\n[LLM] Calling Anthropic API...")
        response = llm.complete(messages=messages, tools=TOOL_DEFINITIONS)
        
        print(f"[LLM] Response content: {response['content'][:500] if response['content'] else '(empty)'}")
        print(f"[LLM] Tool calls: {len(response['tool_calls'])}")
        
        # Add assistant's response to messages
        assistant_msg = {"role": "assistant", "content": response["content"]}
        if response["tool_calls"]:
            assistant_msg["tool_calls"] = response["tool_calls"]
        messages.append(assistant_msg)
        
        # If no tool calls, we're done
        if not response["tool_calls"]:
            print("\n[INFO] No more tool calls - LLM finished.")
            break
        
        # Execute each tool call
        for tc in response["tool_calls"]:
            print(f"\n[TOOL] Executing: {tc['name']}")
            print(f"[TOOL] Arguments: {tc['args']}")
            
            result = executor.execute(tc["name"], tc["args"])
            print(f"[TOOL] Result: {result[:500]}...")
            
            # Add tool result to messages
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result
            })
    
    print("\n" + "=" * 70)
    print("FINAL VERIFICATION")
    print("=" * 70)
    
    # Verify the file exists and contains valid Python
    test_file = "/tmp/test_add.py"
    if os.path.exists(test_file):
        print(f"\n[PASS] File {test_file} exists!")
        
        with open(test_file) as f:
            content = f.read()
        print(f"\n[FILE CONTENT]\n{content}")
        
        # Try to compile/exec the Python to verify it's valid
        try:
            compile(content, test_file, 'exec')
            print("\n[PASS] Python syntax is valid!")
            
            # Try to execute and test the function
            ns = {}
            exec(content, ns)
            if 'add' in ns:
                result = ns['add'](3, 5)
                print(f"[PASS] add(3, 5) = {result}")
                if result == 8:
                    print("[PASS] Function works correctly!")
                else:
                    print(f"[FAIL] Expected 8, got {result}")
            else:
                print("[FAIL] No 'add' function found in file")
        except SyntaxError as e:
            print(f"[FAIL] Syntax error: {e}")
        except Exception as e:
            print(f"[FAIL] Execution error: {e}")
    else:
        print(f"\n[FAIL] File {test_file} does not exist!")
    
    print("\n" + "=" * 70)
    print("CONVERSATION HISTORY")
    print("=" * 70)
    for i, msg in enumerate(messages):
        role = msg["role"]
        if role == "user":
            print(f"\n[MSG {i}] USER:")
            print(msg["content"][:300] + "..." if len(msg.get("content", "")) > 300 else msg.get("content", ""))
        elif role == "assistant":
            print(f"\n[MSG {i}] ASSISTANT:")
            print(msg.get("content", "")[:300] + "..." if len(msg.get("content", "")) > 300 else msg.get("content", ""))
            if msg.get("tool_calls"):
                print(f"  Tool calls: {[tc['name'] for tc in msg['tool_calls']]}")
        elif role == "tool":
            print(f"\n[MSG {i}] TOOL RESULT ({msg['tool_call_id'][:20]}...):")
            print(msg["content"][:200] + "..." if len(msg["content"]) > 200 else msg["content"])
    
    print("\n" + "=" * 70)
    print("TEST COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    # Clean up any previous test file
    if os.path.exists("/tmp/test_add.py"):
        os.remove("/tmp/test_add.py")
        print("[INFO] Cleaned up previous /tmp/test_add.py")
    
    run_loop()
