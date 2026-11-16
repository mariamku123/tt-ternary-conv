SIM ?= icarus
TOPLEVEL_LANG ?= verilog
VERILOG_SOURCES = $(PWD)/../fifo8_core.v $(PWD)/../tt_um_ccen621_fifo.v $(PWD)/tb.v
TOPLEVEL = tb
COCOTB_TEST_MODULES = test_fifo
include $(shell cocotb-config --makefiles)/Makefile.sim
