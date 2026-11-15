# C128 / C128D MMU Riser

Plugs into the MMU socket (U7, MOS 8722) on the C128 / C128D motherboard to add a second MOS 8722 MMU and an ATF16V8B GAL for 256 KB memory expansion.

## Key Differences from the C128DCR Riser
- **GAL Logic**: Uses the exact same ATF16V8 equations and verification as the DCR riser (`c128dcr/mmu-riser/gal/`).
- **Physical Clearance**: The flat C128 case has limited vertical headroom between motherboard socket U7 and the underside of the keyboard assembly. This board requires a low-profile layout (e.g. horizontal component arrangement, low-profile headers, or an offset card).
