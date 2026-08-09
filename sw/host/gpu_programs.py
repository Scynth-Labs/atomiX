"""Reviewed runtime programs used by the GPU host-link client and L1 policy."""

GPU_HALT, GPU_TID, GPU_LI, GPU_MOV, GPU_LDX, GPU_STX = 0, 1, 2, 3, 4, 5
GPU_ADD, GPU_SUB, GPU_MUL = 6, 7, 8
GPU_ADDI, GPU_MULI = 17, 18


def gpu_insn(op, rd=0, ra=0, rb=0, imm=0):
    return ((op << 26) | (rd << 23) | (ra << 20) | (rb << 17) |
            (imm & 0x1FFFF)) & 0xFFFFFFFF


def reviewed_fast_switch_programs(n=10):
    """Return the exact allow-list candidates exercised by --fast-switch."""
    base_a, base_b, base_c = 0, n, 2 * n
    saxpy_data = [i + 1 for i in range(n)] + \
        [100 + 2 * i for i in range(n)] + [0] * n
    saxpy = [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_ADDI, rd=2, ra=0, imm=base_b),
        gpu_insn(GPU_LDX, rd=3, ra=2),
        gpu_insn(GPU_MULI, rd=1, ra=1, imm=3),
        gpu_insn(GPU_ADD, rd=1, ra=1, rb=3),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=base_c),
        gpu_insn(GPU_STX, ra=4, rb=1),
        gpu_insn(GPU_HALT),
    ]
    saxpy_ref = list(saxpy_data)
    for i in range(n):
        saxpy_ref[base_c + i] = 3 * saxpy_data[i] + saxpy_data[base_b + i]

    poly_data = [i - 4 for i in range(n)] + [0] * n
    poly = [
        gpu_insn(GPU_TID, rd=0),
        gpu_insn(GPU_LDX, rd=1, ra=0),
        gpu_insn(GPU_MUL, rd=2, ra=1, rb=1),
        gpu_insn(GPU_MULI, rd=3, ra=1, imm=2),
        gpu_insn(GPU_ADD, rd=2, ra=2, rb=3),
        gpu_insn(GPU_ADDI, rd=2, ra=2, imm=7),
        gpu_insn(GPU_ADDI, rd=4, ra=0, imm=n),
        gpu_insn(GPU_STX, ra=4, rb=2),
        gpu_insn(GPU_HALT),
    ]
    poly_ref = list(poly_data)
    for i in range(n):
        value = poly_data[i]
        poly_ref[n + i] = (value * value + 2 * value + 7) & 0xFFFFFFFF

    return [
        {
            "id": "org.atomix.gpu-program.saxpy-i32-v1",
            "name": "saxpy",
            "workload": "org.atomix.workload.saxpy-i32",
            "revision": 1,
            "words": saxpy,
            "nthreads": n,
            "data": saxpy_data,
            "expected": saxpy_ref,
        },
        {
            "id": "org.atomix.gpu-program.polynomial-i32-v1",
            "name": "polynomial",
            "workload": "org.atomix.workload.polynomial-i32",
            "revision": 1,
            "words": poly,
            "nthreads": n,
            "data": poly_data,
            "expected": poly_ref,
        },
    ]
