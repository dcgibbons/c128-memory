# C128 / C128D SRAM Module

Static RAM replacement for the sixteen 64K×1 DRAMs (4164, 16-pin DIP) on the C128 and C128D motherboards:
- **Bank 0**: U42–U49
- **Bank 1**: U50–U57

## Target Requirements & Constraints
- **Height clearance**: Must fit under the integrated keyboard assembly and tin RF shield of the flat C128.
- **Form factor / Connection**: Interfaces with the 4164 DRAM sockets/bus headers.
- **SRAM**: 128 KB stock operation (Banks 0 and 1) or 256 KB when paired with the MMU riser via `BANK_HI`.
