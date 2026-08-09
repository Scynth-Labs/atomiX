# Hardware achievements

This directory is the evidence ledger for each physical FPGA target. Bring-up
guides describe *how* to run a board; these pages record *what has actually
worked* and what remains pending. Simulation, synthesis, routed timing, and
physical-board results are kept distinct.

| Hardware | Lab availability | Current evidence |
|---|---|---|
| [Tang Primer 25K Dock](tangprimer25k.md) | Owned and connected | CPU, GPU, TPU, and live runtime board-verified |
| [Tang Nano 20K](tangnano20k.md) | Not owned | Simulation and synthesis only |
| [ULX3S-85F](ulx3s-85f.md) | Not owned | Simulation and tool-generated bitstream only |

Archived binaries are immutable evidence, not default build inputs. Each image
must have a recorded SHA-256 digest, source profile, payload, evidence level,
and whether it is safe to load into volatile SRAM. A failed or partial image is
kept only when it is useful for reproducing a named hardware failure and must
never be labelled working.
