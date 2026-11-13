PS5_HOST ?= ps5
PS5_PORT ?= 9021

ifdef PS5_PAYLOAD_SDK
    include $(PS5_PAYLOAD_SDK)/toolchain/prospero.mk
else
    $(error PS5_PAYLOAD_SDK is undefined)
endif

ELF := __PROJECT__.elf

CFLAGS := -Wall -Werror -g -O0
# Bibliotheques systeme de la PS5 : notifications et infos materiel
LIBS := -lSceNotification -lkernel_sys

all: $(ELF)

$(ELF): main.c
	$(CC) $(CFLAGS) -o $@ $^ $(LIBS)

clean:
	rm -f $(ELF)
