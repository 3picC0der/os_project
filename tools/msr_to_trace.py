"""Convert an MSR Cambridge trace in libCacheSim "oracleGeneral" format to our CSV.

The HotOS'23 authors publish the MSR Cambridge block traces at
https://ftp.pdl.cmu.edu/pub/datasets/twemcacheWorkload/cacheDatasets/msr/
Each record is 24 bytes, little-endian:
    uint32 timestamp, uint64 object id (block offset), uint32 size, int64 next access
Like the paper, we treat every object as one equally sized page, and the
trace becomes one process (PID 1). The format has no read/write flag, so
every reference is a read.

usage: python3 tools/msr_to_trace.py msr_hm_0.oracleGeneral.zst data/msr_hm_0.csv
"""
import struct
import subprocess
import sys

RECORD = struct.Struct("<IQIq")


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    src, dst = sys.argv[1], sys.argv[2]
    if src.endswith(".zst"):
        raw = subprocess.run(["zstd", "-dc", src], check=True, capture_output=True).stdout
    else:
        with open(src, "rb") as f:
            raw = f.read()
    if len(raw) % RECORD.size:
        sys.exit(f"{src}: size is not a multiple of {RECORD.size} bytes")

    with open(dst, "w") as out:
        out.write("pid,page,op\n")
        for _, obj_id, _, _ in RECORD.iter_unpack(raw):
            out.write(f"1,{obj_id},R\n")
    print(f"wrote {len(raw) // RECORD.size} references to {dst}")


if __name__ == "__main__":
    main()
