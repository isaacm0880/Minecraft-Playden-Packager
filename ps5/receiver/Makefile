ifndef PS5_PAYLOAD_SDK
$(error Set PS5_PAYLOAD_SDK to the installed ps5-payload-sdk directory)
endif
include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk

ELF := minecraft_playden_receiver.elf
CFLAGS += -std=c11 -DPS5_RECEIVER -Wall -Wextra -Werror -O2

.PHONY: all clean
all: $(ELF)

$(ELF): main.c wire.h paths.h package.h sdk_fs.h vendor/sha-256.c vendor/sha-256.h ../diagnostic/recipe_check.h
	$(CC) $(CFLAGS) -o $@ main.c vendor/sha-256.c

clean:
	rm -f $(ELF)

# Deployment is deliberately manual; see README.md. No build target contacts a PS5.
