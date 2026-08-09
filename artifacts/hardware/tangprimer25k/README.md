# Tang Primer 25K release manifest

Generated FPGA images, kernels, timing reports, and evidence JSON are not
tracked in Git. They grow quickly, remain in Git history after an ordinary
deletion, and are reproducible from the recorded profile and source. During a
lab session they may be retained under the ignored
`artifacts/local/tangprimer25k/` directory.

This manifest pins the most mature physically tested release for each profile:

| Profile and payload | Release SHA-256 | Verified result |
|---|---|---|
| `configs/tangprimer25k.json`, `hello` `.fs` | `bb9ab409ec8f0c0da834672b0c4a6116fb6e18471dba22e285476b78b2065e55` | UART hello and S1 reset pass |
| `configs/tangprimer25k-gpu.json`, `gpu_perf` `.fs` | `a361173ba4a5a82fc98ce4b8445620e9463c891686e33ac508135e2d84a9500b` | Two kernels at four sizes, checked results, `gpu-perf: PASS` |
| `configs/tangprimer25k-tpu.json`, `tpu` `.fs` | `8cbc19c6902f5daf3fb896bc689058c4ae1dff225cdc735ece08912804a70104` | Folded GEMM matches CPU reference, `role tpu-lite: PASS` |
| `configs/tangprimer25k-runtime-gpu.json`, seed-3 loader `.fs` | `62ee2d6d2f833f3bbe29d7af0cac4b64f8a3914db9490d5cdb9b979ce7e329c6` | Physical `FAST SWITCH PASS` without FPGA reload |
| Compact runtime aXos kernel `.bin` | `0d161fbe0a04599817f5f6c321fb3448594863d3fc0187b9a8bc7d71804a4473` | CRC-verified upload and two checked GPU programs |

All board development uses reversible SRAM configuration only. Do not add
`-f` to `openFPGALoader`, and do not use `make flash` until the recovery
checklist explicitly permits it.
