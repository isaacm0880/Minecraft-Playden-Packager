# Uses the upstream ps5-payload-dev/sdk toolchain; no vendored SDK.
ifndef PS5_PAYLOAD_SDK
$(error Set PS5_PAYLOAD_SDK to your installed ps5-payload-sdk directory)
endif
include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk

ELF := minecraft_playden_diagnostic.elf
CFLAGS += -Wall -Wextra -Werror -O2
PS5_HOST ?= ps5
PS5_PORT ?= 9021

.PHONY: all clean test
all: $(ELF)

$(ELF): main.c recipe_check.h
	$(CC) $(CFLAGS) -o $@ $<

clean:
	rm -f $(ELF)

# Explicit opt-in: requires an already running compatible ELF loader.
test: $(ELF)
	$(PS5_DEPLOY) -h $(PS5_HOST) -p $(PS5_PORT) $(ELF)
