import torch
import torch.nn as nn
import torch.nn.functional as F
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, List
import math

@dataclass
class Args:
  emb_dim : int = 2048
  hidden_emb : int = 6144
  initializer_range : float = 0.02
  head_dim : int = 128
  n_layers : int = 28
  n_heads : int = 16
  n_kv_head : int = 8
  context_length : int = 40960
  eps : int = 1e-06
  tie_word_embedding : bool = True
  vocab_size : int = 151936
  qk_norm : bool = True
  rope_theta : float = 1_000_000.0
  dtype: torch.dtype = torch.bfloat16
  sliding_window : int = 10240
  kv_quant_bits: int = 4
  kv_quant_mode: str = "prod"
def _gaussian_lloyd_max_centroids(n_levels: int) -> torch.Tensor:
    import torch
    c = torch.linspace(-3.0, 3.0, n_levels)
    for _ in range(300):
        b = (c[:-1] + c[1:]) / 2.0
        boundaries = torch.cat([torch.tensor([-1e9]), b, torch.tensor([1e9])])
        lo = boundaries[:-1]
        hi = boundaries[1:]
        phi_lo = torch.exp(-0.5 * lo**2) / math.sqrt(2 * math.pi)
        phi_hi = torch.exp(-0.5 * hi**2) / math.sqrt(2 * math.pi)
        Phi_lo = 0.5 * (1 + torch.erf(lo / math.sqrt(2)))
        Phi_hi = 0.5 * (1 + torch.erf(hi / math.sqrt(2)))
        denom = (Phi_hi - Phi_lo).clamp(min=1e-12)
        c_new = (phi_lo - phi_hi) / denom
        if torch.max(torch.abs(c_new - c)) < 1e-8:
            c = c_new
            break
        c = c_new
    return c.sort()[0]


_CODEBOOK_CACHE: Dict[int, torch.Tensor] = {}

def _get_codebook(bits: int) -> torch.Tensor:
    if bits not in _CODEBOOK_CACHE:
        n = 2 ** bits
        _CODEBOOK_CACHE[bits] = _gaussian_lloyd_max_centroids(n)
    return _CODEBOOK_CACHE[bits]


class TurboQuantMSE(nn.Module):
    def __init__(self, dim: int, bits: int, dtype: torch.dtype = torch.float32):
        super().__init__()
        self.dim   = dim
        self.bits  = bits
        self.dtype = dtype

        raw = torch.randn(dim, dim)
        Pi, _ = torch.linalg.qr(raw)
        self.register_buffer("Pi", Pi.to(dtype))

        cb = _get_codebook(bits) / math.sqrt(dim)
        self.register_buffer("codebook", cb.to(dtype))

    def quant(self, x: torch.Tensor) -> torch.Tensor:
        orig_dtype = x.dtype
        x = x.to(self.dtype)
        y = x @ self.Pi.T
        diff = y.unsqueeze(-1) - self.codebook
        idx  = diff.abs().argmin(dim=-1).to(torch.int32)
        return idx

    def dequant(self, idx: torch.Tensor) -> torch.Tensor:
        y_tilde = self.codebook[idx.long()]
        x_tilde = y_tilde @ self.Pi
        return x_tilde

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.dequant(self.quant(x))


class TurboQuantProd(nn.Module):

    def __init__(self, dim: int, bits: int, dtype: torch.dtype = torch.float32):
        super().__init__()
        assert bits >= 2, "TurboQuantProd needs bits >= 2 (uses bits-1 for MSE stage)"
        self.dim   = dim
        self.bits  = bits
        self.dtype = dtype

        self.mse_quant = TurboQuantMSE(dim, bits - 1, dtype)

        S = torch.randn(dim, dim)
        self.register_buffer("S", S.to(dtype))

    def quant(self, x: torch.Tensor):
        
        orig_dtype = x.dtype
        x = x.to(self.dtype)

        idx     = self.mse_quant.quant(x)
        x_mse   = self.mse_quant.dequant(idx)

        r       = x - x_mse
        gamma   = r.norm(dim=-1, keepdim=True)

        Sr  = r @ self.S.T
        qjl = Sr.sign().to(torch.int8)
        qjl = qjl + (qjl == 0).to(torch.int8)

        return idx, qjl, gamma.to(self.dtype)

    def dequant(self, idx: torch.Tensor,
                qjl: torch.Tensor,
                gamma: torch.Tensor) -> torch.Tensor:
        x_mse   = self.mse_quant.dequant(idx)

        scale   = math.sqrt(math.pi / 2) / self.dim
        qjl_f   = qjl.to(self.dtype)
        x_qjl   = scale * gamma * (qjl_f @ self.S)

        return x_mse + x_qjl

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        idx, qjl, gamma = self.quant(x)
        return self.dequant(idx, qjl, gamma)


class TurboQuantKVCache:
    def __init__(
        self,
        batch_size:  int,
        max_seq_len: int,
        n_kv_heads:  int,
        head_dim:    int,
        bits:        int,
        mode:        str,
        dtype:       torch.dtype,
        device:      torch.device,
    ):
        self.batch_size  = batch_size
        self.max_seq_len = max_seq_len
        self.n_kv_heads  = n_kv_heads
        self.head_dim    = head_dim
        self.bits        = bits
        self.mode        = mode
        self.cache_len   = 0

        quant_cls = TurboQuantMSE if mode == "mse" else TurboQuantProd
        self.k_quantizer = quant_cls(head_dim, bits, dtype=torch.float32).to(device)
        self.v_quantizer = quant_cls(head_dim, bits, dtype=torch.float32).to(device)

        shape = (batch_size, n_kv_heads, max_seq_len, head_dim)

        self.k_idx = torch.zeros(*shape, dtype=torch.int32, device=device)
        self.v_idx = torch.zeros(*shape, dtype=torch.int32, device=device)

        if mode == "prod":
            self.k_qjl   = torch.zeros(*shape,     dtype=torch.int8,   device=device)
            self.v_qjl   = torch.zeros(*shape,     dtype=torch.int8,   device=device)
            self.k_gamma = torch.zeros(*shape[:-1], 1, dtype=torch.float32, device=device)
            self.v_gamma = torch.zeros(*shape[:-1], 1, dtype=torch.float32, device=device)

        self._float_dtype = dtype

    def _quant_store(self, x: torch.Tensor, quantizer, idx_buf, pos,
                     qjl_buf=None, gamma_buf=None):
        B, H, T, D = x.shape
        xf = x.to(torch.float32)

        if self.mode == "mse":
            idx = quantizer.quant(xf)
            idx_buf[:, :, pos:pos+T, :] = idx
        else:
            idx, qjl, gamma = quantizer.quant(xf)
            idx_buf  [:, :, pos:pos+T, :] = idx
            qjl_buf  [:, :, pos:pos+T, :] = qjl
            gamma_buf[:, :, pos:pos+T, :] = gamma

    def _dequant_read(self, quantizer, idx_buf, end,
                      qjl_buf=None, gamma_buf=None) -> torch.Tensor:
        idx = idx_buf[:, :, :end, :]
        if self.mode == "mse":
            out = quantizer.dequant(idx)
        else:
            qjl   = qjl_buf  [:, :, :end, :]
            gamma = gamma_buf[:, :, :end, :]
            out   = quantizer.dequant(idx, qjl, gamma)
        return out.to(self._float_dtype)

    def update(self, keys: torch.Tensor, values: torch.Tensor
               ) -> Tuple[torch.Tensor, torch.Tensor]:
        T = keys.shape[2]
        if self.mode == "mse":
            self._quant_store(keys,   self.k_quantizer, self.k_idx, self.cache_len)
            self._quant_store(values, self.v_quantizer, self.v_idx, self.cache_len)
        else:
            self._quant_store(keys,   self.k_quantizer, self.k_idx, self.cache_len,
                              self.k_qjl, self.k_gamma)
            self._quant_store(values, self.v_quantizer, self.v_idx, self.cache_len,
                              self.v_qjl, self.v_gamma)

        self.cache_len += T

        k_out = self._dequant_read(self.k_quantizer, self.k_idx, self.cache_len,
                                   getattr(self, "k_qjl",   None),
                                   getattr(self, "k_gamma",  None))
        v_out = self._dequant_read(self.v_quantizer, self.v_idx, self.cache_len,
                                   getattr(self, "v_qjl",   None),
                                   getattr(self, "v_gamma",  None))
        return k_out, v_out

    def reset(self):
        self.cache_len = 0
        self.k_idx.zero_()
        self.v_idx.zero_()
        if self.mode == "prod":
            self.k_qjl.zero_()
            self.v_qjl.zero_()
            self.k_gamma.zero_()
            self.v_gamma.zero_()


class KVCache:
    def __init__(self, batch_size: int, max_seq_len: int,
                 n_kv_heads: int, head_dim: int,
                 dtype: torch.dtype, device: torch.device):
        self.k_cache = torch.zeros(batch_size, n_kv_heads, max_seq_len, head_dim,
                                   dtype=dtype, device=device)
        self.v_cache = torch.zeros(batch_size, n_kv_heads, max_seq_len, head_dim,
                                   dtype=dtype, device=device)
        self.cache_len = 0

    def update(self, keys: torch.Tensor, values: torch.Tensor
               ) -> Tuple[torch.Tensor, torch.Tensor]:
        seq_len = keys.shape[2]
        self.k_cache[:, :, self.cache_len : self.cache_len + seq_len, :] = keys
        self.v_cache[:, :, self.cache_len : self.cache_len + seq_len, :] = values
        self.cache_len += seq_len
        return (
            self.k_cache[:, :, : self.cache_len, :],
            self.v_cache[:, :, : self.cache_len, :],
        )

    def reset(self):
        self.cache_len = 0
        self.k_cache.zero_()
        self.v_cache.zero_()


class FeedForward(nn.Module):
  def __init__(self, args : Args) -> None:
    super().__init__()
    self.fc1 = nn.Linear(args.emb_dim, args.hidden_emb, bias=False, dtype=args.dtype)
    self.fc2 = nn.Linear(args.emb_dim, args.hidden_emb, bias=False, dtype=args.dtype)
    self.fc3 = nn.Linear(args.hidden_emb, args.emb_dim, bias=False, dtype=args.dtype)
  def forward(self, x : torch.Tensor) -> torch.Tensor:
    return self.fc3(F.silu(self.fc1(x)) * self.fc2(x))


class RMSNorm(nn.Module):
    def __init__(self, emb_dim : int, eps=1e-6, bias=False, qwen3_compatible=True):
        super().__init__()
        self.eps = eps
        self.qwen3_compatible = qwen3_compatible
        self.scale = nn.Parameter(torch.ones(emb_dim))
        self.shift = nn.Parameter(torch.zeros(emb_dim)) if bias else None

    def forward(self, x):
        input_dtype = x.dtype
        if self.qwen3_compatible:
            x = x.to(torch.float32)
        variance = x.pow(2).mean(dim=-1, keepdim=True)
        norm_x = x * torch.rsqrt(variance + self.eps)
        norm_x = norm_x * self.scale
        if self.shift is not None:
            norm_x = norm_x + self.shift
        return norm_x.to(input_dtype)


def compute_rope_params(head_dim, theta_base=10_000, context_length=4096, dtype=torch.float32):
    assert head_dim % 2 == 0, "Embedding dimension must be even"
    inv_freq = 1.0 / (theta_base ** (torch.arange(0, head_dim, 2, dtype=dtype)[: (head_dim // 2)].float() / head_dim))
    positions = torch.arange(context_length, dtype=dtype)
    angles = positions.unsqueeze(1) * inv_freq.unsqueeze(0)
    angles = torch.cat([angles, angles], dim=1)
    cos = torch.cos(angles)
    sin = torch.sin(angles)
    return cos, sin


def apply_rope(x, cos, sin, start_pos: int = 0):
    batch_size, num_heads, seq_len, head_dim = x.shape
    assert head_dim % 2 == 0, "Head dimension must be even"
    x1 = x[..., : head_dim // 2]
    x2 = x[..., head_dim // 2 :]
    cos_slice = cos[start_pos : start_pos + seq_len, :].unsqueeze(0).unsqueeze(0)
    sin_slice = sin[start_pos : start_pos + seq_len, :].unsqueeze(0).unsqueeze(0)
    rotated = torch.cat((-x2, x1), dim=-1)
    x_rotated = (x * cos_slice) + (rotated * sin_slice)
    return x_rotated.to(dtype=x.dtype)


class GroupedQueryAttention(nn.Module):
  def __init__(self, num_heads: int, emb_dim: int, num_kv_groups: int,
               bias: Optional[bool] = False, head_dim: Optional[int] = None,
               qk_norm: Optional[bool] = False, dtype=None) -> None:
     super().__init__()
     assert num_heads % num_kv_groups == 0, "num_heads must be divisible by num_kv_groups"
     self.num_heads = num_heads
     self.emb_dim = emb_dim
     self.num_kv_groups = num_kv_groups
     self.group_size = num_heads // num_kv_groups
     if head_dim is None:
       assert emb_dim % num_heads == 0
       head_dim = emb_dim // num_heads
     self.head_dim = head_dim
     self.d_out = self.num_heads * self.head_dim
     self.q_proj   = nn.Linear(emb_dim, self.d_out,              bias=False, dtype=dtype)
     self.k_proj   = nn.Linear(emb_dim, num_kv_groups * head_dim, bias=False, dtype=dtype)
     self.v_proj   = nn.Linear(emb_dim, num_kv_groups * head_dim, bias=False, dtype=dtype)
     self.out_proj = nn.Linear(self.d_out, emb_dim,              bias=False, dtype=dtype)
     if qk_norm:
      self.q_norm = RMSNorm(head_dim, eps=1e-6)
      self.k_norm = RMSNorm(head_dim, eps=1e-6)
     else:
      self.q_norm = self.k_norm = None

  def forward(
      self,
      x: torch.Tensor,
      mask,
      cos: torch.Tensor,
      sin: torch.Tensor,
      start_pos: int = 0,
      kv_cache=None,
  ) -> torch.Tensor:
    B, T, C = x.shape

    queries = self.q_proj(x)
    keys    = self.k_proj(x)
    values  = self.v_proj(x)

    queries = queries.view(B, T, self.num_heads,    self.head_dim).transpose(1, 2)
    keys    = keys.view   (B, T, self.num_kv_groups, self.head_dim).transpose(1, 2)
    values  = values.view (B, T, self.num_kv_groups, self.head_dim).transpose(1, 2)

    if self.q_norm:
        queries = self.q_norm(queries)
    if self.k_norm:
        keys = self.k_norm(keys)

    queries = apply_rope(queries, cos, sin, start_pos=start_pos)
    keys    = apply_rope(keys,    cos, sin, start_pos=start_pos)

    if kv_cache is not None:
        keys, values = kv_cache.update(keys, values)
        keys   = keys.to(queries.dtype)
        values = values.to(queries.dtype)

    keys   = keys.repeat_interleave(self.group_size, dim=1)
    values = values.repeat_interleave(self.group_size, dim=1)

    is_causal = (kv_cache is None) or (T > 1)

    context_vector = F.scaled_dot_product_attention(
        queries, keys, values,
        attn_mask=None,
        dropout_p=0.0,
        is_causal=is_causal,
    )
    context_vector = context_vector.transpose(1, 2).contiguous().view(B, T, self.d_out)
    return self.out_proj(context_vector)


class TransformerBlock(nn.Module):
  def __init__(self, args: Args) -> None:
    super().__init__()
    self.attn = GroupedQueryAttention(
        num_heads=args.n_heads, emb_dim=args.emb_dim,
        num_kv_groups=args.n_kv_head, head_dim=args.head_dim,
        qk_norm=args.qk_norm, dtype=args.dtype
    )
    self.ffn    = FeedForward(args)
    self.norm_1 = RMSNorm(args.emb_dim, eps=args.eps)
    self.norm_2 = RMSNorm(args.emb_dim, eps=args.eps)

  def forward(
      self,
      x: torch.Tensor,
      mask,
      sin: torch.Tensor,
      cos: torch.Tensor,
      start_pos: int = 0,
      kv_cache=None,
  ) -> torch.Tensor:
    shortcut = x
    x = self.norm_1(x)
    x = self.attn(x, mask, cos, sin, start_pos=start_pos, kv_cache=kv_cache)
    x += shortcut
    shortcut = x
    x = self.norm_2(x)
    x = self.ffn(x)
    x += shortcut
    return x


class Qwen3Model(nn.Module):
    def __init__(self, args: Args) -> None:
        super().__init__()
        self.args = args
        self.tok_emb = nn.Embedding(args.vocab_size, args.emb_dim, dtype=args.dtype)
        self.trf_blocks = nn.ModuleList(
            [TransformerBlock(args) for _ in range(args.n_layers)]
        )
        self.final_norm = RMSNorm(args.emb_dim)
        self.out_head   = nn.Linear(args.emb_dim, args.vocab_size, bias=False, dtype=args.dtype)

        head_dim = args.head_dim if args.head_dim is not None else args.emb_dim // args.n_heads
        cos, sin = compute_rope_params(
            head_dim=head_dim,
            theta_base=args.rope_theta,
            context_length=args.context_length,
        )
        self.register_buffer("cos", cos, persistent=False)
        self.register_buffer("sin", sin, persistent=False)

        self.kv_caches: Optional[List] = None
        self.apply(self._init_weights)
    def setup_kv_cache(
        self,
        batch_size: int,
        max_seq_len: int,
        device: torch.device,
        use_turbo_quant: bool = False,
    ) -> None:
        head_dim = self.args.head_dim if self.args.head_dim else self.args.emb_dim // self.args.n_heads

        if use_turbo_quant:
            self.kv_caches = [
                TurboQuantKVCache(
                    batch_size  = batch_size,
                    max_seq_len = max_seq_len,
                    n_kv_heads  = self.args.n_kv_head,
                    head_dim    = head_dim,
                    bits        = self.args.kv_quant_bits,
                    mode        = self.args.kv_quant_mode,
                    dtype       = self.args.dtype,
                    device      = device,
                )
                for _ in range(self.args.n_layers)
            ]
        else:
            self.kv_caches = [
                KVCache(
                    batch_size  = batch_size,
                    max_seq_len = max_seq_len,
                    n_kv_heads  = self.args.n_kv_head,
                    head_dim    = head_dim,
                    dtype       = self.args.dtype,
                    device      = device,
                )
                for _ in range(self.args.n_layers)
            ]

    def reset_kv_cache(self) -> None:
        if self.kv_caches is not None:
            for cache in self.kv_caches:
                cache.reset()

    def forward(
        self,
        in_idx: torch.Tensor,
        start_pos: int = 0,
    ) -> torch.Tensor:
        x = self.tok_emb(in_idx)
        T = x.shape[1]
        mask = self._mask_create(T, x.device, "casual") if T > 1 else None
        for i, block in enumerate(self.trf_blocks):
            kv_cache = self.kv_caches[i] if self.kv_caches is not None else None
            x = block(x, mask, self.sin, self.cos, start_pos=start_pos, kv_cache=kv_cache)

        x = self.final_norm(x)
        return self.out_head(x.to(self.args.dtype))

    def generate_with_cache(
        self,
        token_ids: torch.Tensor,
        max_new_tokens: int,
        eos_token_id: Optional[int] = None,
        max_seq_len: int = 2048,
        use_turbo_quant: bool = False,
    ):
        self.eval()
        device = token_ids.device
        B      = token_ids.shape[0]

        self.setup_kv_cache(
            batch_size=B,
            max_seq_len=max_seq_len,
            device=device,
            use_turbo_quant=use_turbo_quant,
        )

        with torch.no_grad():
            prompt_len = token_ids.shape[1]
            logits     = self.forward(token_ids, start_pos=0)
            next_token = torch.argmax(logits[:, -1, :], dim=-1, keepdim=True)

            if eos_token_id is not None and torch.all(next_token == eos_token_id):
                return
            yield next_token

            cur_pos = prompt_len
            for _ in range(max_new_tokens - 1):
                logits     = self.forward(next_token, start_pos=cur_pos)
                next_token = torch.argmax(logits[:, -1, :], dim=-1, keepdim=True)
                cur_pos   += 1

                if eos_token_id is not None and torch.all(next_token == eos_token_id):
                    break
                yield next_token

                if cur_pos >= max_seq_len - 1:
                    break

    def generate_no_cache(
        self,
        token_ids: torch.Tensor,
        max_new_tokens: int,
        eos_token_id: Optional[int] = None,
    ):
        self.eval()
        self.kv_caches = None
        with torch.no_grad():
            for _ in range(max_new_tokens):
                out        = self.forward(token_ids)[:, -1]
                next_token = torch.argmax(out, dim=-1, keepdim=True)
                if eos_token_id is not None and torch.all(next_token == eos_token_id):
                    break
                yield next_token
                token_ids = torch.cat([token_ids, next_token], dim=1)

    def _init_weights(self, module) -> None:
        if isinstance(module, nn.Linear):
            nn.init.normal_(module.weight, mean=0.0, std=self.args.initializer_range)
            if module.bias is not None:
                nn.init.zeros_(module.bias)
        elif isinstance(module, nn.Embedding):
            nn.init.normal_(module.weight, mean=0.0, std=self.args.initializer_range)

    def _mask_create(self, seq_len: int, device: torch.device, name: str) -> torch.Tensor:
        if name == "sliding_window":
            window = self.args.sliding_window
            ones   = torch.ones(seq_len, seq_len, dtype=torch.bool, device=device)
            return ~torch.triu(torch.tril(ones, diagonal=0), diagonal=-window)
        else:
            return torch.triu(torch.ones(seq_len, seq_len, dtype=torch.bool, device=device), diagonal=1)