import os
from transformers import AutoModelForCausalLM, AutoTokenizer, TextStreamer
import torch

# Your corrected local path
local_model_dir = "local-ai/Qwen3-1.7B"

print("Loading model weights directly from local drive...")

tokenizer = AutoTokenizer.from_pretrained(local_model_dir)

model = AutoModelForCausalLM.from_pretrained(
    local_model_dir,
    torch_dtype="auto", 
    device_map="auto"   
)

print("Model successfully loaded into your RTX 4060!\n")

# FIXED: Base models need a starting sentence to "complete", not a chat question.
prompt = "The capital of Türkiye is Ankara. It is famous for"

# Convert text directly to tensor numbers
model_inputs = tokenizer(prompt, return_tensors="pt").to(model.device)

# NEW: Set up a streamer to print words to the console as they are generated
streamer = TextStreamer(tokenizer, skip_prompt=True, skip_special_tokens=True)

print("Generating continuation...\n")
print("--- AI Response ---")

# The streamer will handle the printing automatically!
generated_ids = model.generate(
    **model_inputs, 
    max_new_tokens=150, # Lowered so it doesn't ramble forever
    temperature=0.7,
    streamer=streamer   # This streams the text live
)

print("\n-------------------")
print("Generation finished.")