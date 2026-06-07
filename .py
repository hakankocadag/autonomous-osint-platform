import sys
import torch

print("=" * 50)
print("SYSTEM & ENV CHECK")
print("=" * 50)
print(f"Python Executable: {sys.executable}")
print(f"PyTorch Version:   {torch.__version__}")
print(f"CUDA Built-in:     {torch.version.cuda}")

print("\n" + "=" * 50)
print("GPU DETECTION")
print("=" * 50)
cuda_available = torch.cuda.is_available()
print(f"CUDA Available:    {cuda_available}")

if cuda_available:
    device_name = torch.cuda.get_device_name(0)
    device_count = torch.cuda.device_count()
    current_device = torch.cuda.current_device()
    
    print(f"Total GPUs Found:  {device_count}")
    print(f"Active GPU ID:     {current_device}")
    print(f"Active GPU Name:   {device_name}")
    
    # Check VRAM limits (Useful for your RTX 4060)
    total_memory = torch.cuda.get_device_properties(0).total_memory / (1024**3)
    print(f"Total VRAM:        {total_memory:.2f} GB")
    
    print("\n" + "=" * 50)
    print("VRAM FUNCTIONAL TEST")
    print("=" * 50)
    try:
        # Create a tiny test tensor and push it to the RTX 4060
        test_tensor = torch.tensor([1.0, 2.0, 3.0]).cuda()
        print(f"Tensor Location:   {test_tensor.device}")
        print("Status:            Success! PyTorch can allocate VRAM.")
    except Exception as e:
        print(f"Status:            Failed during VRAM allocation.")
        print(f"Error Message:     {e}")
else:
    print("\n[!] CRITICAL: PyTorch still cannot see your GPU.")
    print("    Ensure your NVIDIA drivers are updated and restart your IDE.")
print("=" * 50)
