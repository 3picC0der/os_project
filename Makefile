CC      ?= cc
CFLAGS  ?= -std=c11 -O2 -Wall -Wextra -pedantic
LDLIBS  = -lm

all: bin/vmsim bin/vmgen bin/test_sim

bin/vmsim: src/main.c src/sim.c src/trace.c src/sim.h src/trace.h | bin
	$(CC) $(CFLAGS) -o $@ src/main.c src/sim.c src/trace.c $(LDLIBS)

bin/vmgen: src/gen.c | bin
	$(CC) $(CFLAGS) -o $@ src/gen.c $(LDLIBS)

bin/test_sim: tests/test_sim.c src/sim.c src/trace.c src/sim.h src/trace.h | bin
	$(CC) $(CFLAGS) -Isrc -o $@ tests/test_sim.c src/sim.c src/trace.c $(LDLIBS)

bin:
	mkdir -p bin

# Unit tests: every case from the proposal's test plan plus the invariants.
test: bin/test_sim
	./bin/test_sim

# All experiments: generates traces, runs every policy, writes CSVs and charts.
experiments: bin/vmsim bin/vmgen
	python3 analysis/run_experiments.py

# Progress report PDF from docs/progress_report.html (needs Google Chrome).
# Screenshots saved in docs/screenshots/ are picked up automatically.
CHROME ?= /Applications/Google Chrome.app/Contents/MacOS/Google Chrome
report:
	cd docs && "$(CHROME)" --headless=new --disable-gpu --no-pdf-header-footer \
	    --print-to-pdf=ICS433_Phase2_Progress_Report.pdf "file://$$PWD/progress_report.html"

clean:
	rm -rf bin

.PHONY: all test experiments report clean
