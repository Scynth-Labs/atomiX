# Checked-in fuzz seeds

Inputs that once failed, kept so they are replayed on every run.  A crash the
fuzzer found and nobody saved is a crash that gets rediscovered.

| Seed | What it was |
|---|---|
| `entry-in-non-executable-segment.elf` | `e_entry` inside a mapped but non-executable PT_LOAD segment. The loader checked the entry page was mapped and not that it could be executed, so the image was accepted and the task died on its first instruction fetch instead of being rejected. Found at 15,158 executions; fixed in `loader.c` by tracking whether `e_entry` falls in a `PF_X` segment. |

`make -C sim/fuzz run` copies these in alongside the generated corpus, so a
regression here fails in the first second rather than after a long search.
