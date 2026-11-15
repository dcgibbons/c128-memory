# Commodore 128 / C128D memory boards

Open-hardware memory upgrade boards for the **Commodore 128** (flat keyboard model) and **Commodore 128D** (desktop chassis model with integrated drive and power supply).

Both machines share the same underlying motherboard architecture (`310378` core layout) and DRAM subsystem. Boards designed here are low-profile to clear the integrated keyboard and RF shield of the flat C128, which means they drop directly into the roomier C128D case as well.

| Board | Description |
|---|---|
| [`sram/`](sram/) | **SRAM module**: replaces the sixteen 64K×1 DRAMs (U42–U57, 4164) with static RAM; low profile to clear keyboard |
| [`mmu-riser/`](mmu-riser/) | **MMU riser**: plugs into U7 (MOS 8722) with a second 8722 and an ATF16V8B; low-profile horizontal or offset layout for keyboard clearance |
