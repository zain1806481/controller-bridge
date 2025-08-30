# controller-bridge

Share Xbox controller input across multiple PCs using Input Leap/Barrier.

**Concept**: Idea from Shane Collins. This project demonstrates bridging a physical game controller across PCs by reading input on one machine and virtualizing it on another, following your mouse across screens. Produced with the help of ChatGPT.

## How it works

1. Install ViGEmBus and optional HidHide on both PCs.
2. Copy this repository to both PCs.
3. Pair or plug your controller into one PC (the host).
4. Run `run.bat` on both PCs. This script installs dependencies and starts the bridge automatically.
5. Move your mouse between screens. The controller follows the active screen. Press F12 on the host to manually toggle if needed.

See `bridge.py` and `run.bat` for technical details and customization.
