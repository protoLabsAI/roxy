# protoApp → iPad: the pivot to an iPad-native app (brief for Roxy)

**Owner:** Josh · **Drafted by:** operator research pass · **Date:** 2026-06-01
**For:** Roxy to decompose into epics → milestones → features on the protoMaker board.

---

## 1. Objective

**protoApp becomes an iPad-native app.** Going forward the **iPad is the only target** —
desktop (macOS/Win/Linux) is no longer a maintained product surface (a macOS build may be
kept *only* as a fast dev-iteration convenience, not a shipped artifact). Today protoApp is
a Tauri 2 **desktop** app: a React UI talking to an **in-process Rust axum server** exposing
an OpenAI-compatible `/v1/*` surface, backed by native engines (llama.cpp LLM, whisper.cpp
STT, Kokoro TTS) with Metal, plus a **Python sidecar** (`orbis-sidecar`). We are re-homing
this onto iPadOS and **replacing the Python sidecar with an in-process Rust agent**.

### Definition of done (v1)
- A signed protoApp `.ipa` installs and launches on the target iPad (signing certs already in hand).
- React UI renders full-screen, touch-usable, safe-area aware.
- **Chat works on-device** against a local LLM (Metal) — real streamed tokens.
- **Transcribe (STT)** works from the iPad mic; **Speak (TTS)** works (native synth v1).
- **In-process Rust agent** runs on-device (replacing ORBIS Python) — at least a minimal
  agent loop + memory + A2A, no subprocess.
- Survives memory pressure; uses no prohibited APIs; iOS build wired into **macOS CI**.

---

## 2. Target device & hard constraints

- **Target device: M4 iPad Pro (~16 GB RAM).** Apple-silicon Metal GPU. Older/low-RAM iPads
  are **not** a concern — optimize for this device.
- **Model:** with ~16 GB and the `com.apple.developer.kernel.increased-memory-limit` +
  **Extended Virtual Addressing** entitlements, the current **Qwen3-4B-Instruct-2507 (Q4, ~2.5 GB)
  is viable on-device** — keep it (mmap the GGUF; still budget KV cache + webview). No need to
  downsize for v1. Be ready to handle Jetsam gracefully under pressure.
- **Build host:** iOS builds **require macOS + full Xcode** + CocoaPods; `aarch64-apple-ios`
  can't be cross-compiled from Linux (`xcrun`). **CI must run on a macOS runner** (available — wire it up).
- **No bundled-binary execution / no subprocesses** (App Store §2.5.2 + sandbox). Native code is
  **linked as a library**, never exec'd. This is *why* the Python sidecar is replaced by in-process Rust.
- **WKWebView** hosts the UI. `getUserMedia` mic works **with `NSMicrophoneUsageDescription`** (per-call
  permission popup; capture stops in background).
- **Distribution:** signing certs/provisioning **already available** — not a blocker. Ship via the
  existing cert flow (TestFlight or direct install) as needed.

---

## 3. Current architecture vs iPad — gap analysis

| Component | Today | iPad verdict | Action |
|---|---|---|---|
| React UI (Vite/shadcn/Zustand) | desktop window 800×600 | ✅ runs in WKWebView | rebuild layout for touch/full-screen/safe-areas |
| In-process axum `/v1/*` server | localhost port | ✅ in-process allowed | bind 127.0.0.1; verify WKWebView→localhost on device early |
| `openai` JS SDK → localhost | works | ✅ | keep |
| LLM: `llama-cpp-2`/`-sys-2` (=0.1.143) + Metal | desktop | ⚠️ **unproven on iOS** | **SPIKE**: build `llama-cpp-sys-2` for `aarch64-apple-ios` + Metal (embed shader lib) |
| STT: `whisper-rs` 0.16 | desktop | ⚠️ Rust-binding iOS build unproven (whisper.cpp itself ships iOS examples) | **SPIKE**: whisper-rs on iOS, else native Speech fallback |
| TTS: `kokoros` + **espeak-ng (GPL)** | desktop | ❌ GPL + not iOS-shaped | **swap** → AVSpeechSynthesizer v1; kokoro-onnx later |
| `orbis-sidecar` (spawns Python) | desktop | ❌ forbidden | **replace with in-process Rust agent** (see D1 / E_AGENT) |
| `tray-icon`, opener, fixed window, CUDA | desktop | ❌ N/A on iPad | **remove** (iPad-only) — keep behind a dev-only `cfg` only if it helps mac dev iteration |
| Model download (~2.5 GB first use) | `~/.cache/protoapp` | ⚠️ sandbox path + size | app-container path + background download + warmup UX |

---

## 4. Architectural decisions (confirmed with Josh)

- **D1 — Replace the Python sidecar with an in-process Rust agent.** `orbis-sidecar` can't ship
  (subprocess ban). Re-implement the needed ORBIS capabilities — **agent loop, memory, A2A** — in
  **Rust, in-process**, inside the Tauri app. This is a major workstream and needs its own discovery
  against the reference Python implementation at **`~/dev/ORBIS`** (scope the minimum viable agent for v1).
- **D2 — Keep the in-process axum `/v1/*` server.** Cleanest seam; in-process (no subprocess); UI keeps
  the `openai` SDK on `127.0.0.1`. The in-process Rust agent attaches here, not over a spawned socket.
- **D3 — LLM stays llama.cpp + Metal; the build is the first spike.** Get `llama-cpp-sys-2` building for
  `aarch64-apple-ios` with `GGML_USE_METAL` + embedded Metal shaders. If the `=0.1.143` pin can't target
  iOS: bump/patch the sys crate or vendor the build. Fallback only if truly blocked: an iOS-proven runtime
  (MLC-LLM / llama.cpp Swift bridge). **De-risk before building on top.**
- **D4 — TTS: AVSpeechSynthesizer for v1.** Drop `kokoros` (espeak-ng GPL is incompatible with our MIT/Apache
  shipping; crate not iOS-ready). Native AVSpeechSynthesizer is free/on-device/no-model (no streaming — OK v1).
  Revisit kokoro-onnx / sherpa-onnx (Apache, espeak-free) later for quality/streaming.
- **D5 — STT: whisper.cpp on-device preferred, native Speech (SFSpeechRecognizer) as fallback** behind the
  same `/v1/audio/transcriptions` seam, decided by the E1 spike result.
- **D6 — iPad-only: remove desktop-only surface.** `tray-icon`, opener, fixed-window, CUDA are gone (not just
  gated). Keep a macOS dev build only if it speeds iteration — it is **not** a shipped product.
- **D7 — Keep Qwen3-4B on iPad** (16 GB device + memory entitlements). No model downsizing for v1.

---

## 5. Proposed epics (Roxy to refine into milestones/features)

- **E1 — Engine portability spikes (DE-RISK FIRST).** Prove/disprove `llama-cpp-sys-2` + `whisper-rs` build
  & run on `aarch64-apple-ios` with Metal. Time-boxed; output = go/no-go + exact flags/patches. Gates everything.
- **E2 — iPad platform bring-up (walking skeleton).** `tauri ios init`, signing wired, **stub-engine** build
  launching on the iPad with the React UI rendering. Shell on device, no real inference yet.
- **E3 — Strip to iPad-only.** Remove `orbis-sidecar`, `tray-icon`, opener, window/CUDA from the shipped target;
  iOS target compiles clean; (optional) dev-only mac build still works. CI guard.
- **E4 — LLM on device.** Wire the E1 result in: Metal inference, Qwen3-4B with memory entitlements + mmap,
  first-run/background model download into the app container, warmup UX. Real streamed chat on the iPad.
- **E5 — Voice I/O on device.** STT from mic (whisper.cpp or native) + `NSMicrophoneUsageDescription` +
  AVAudioSession; TTS via AVSpeechSynthesizer. Transcribe + Speak tabs working on device.
- **E6 — In-process Rust agent (replaces ORBIS Python).** Discovery against `~/dev/ORBIS`, then a minimal
  Rust agent (loop + memory + A2A) running in-process and attached to the `/v1` server. Large; likely its own
  milestone track. Scope the MVP agent for v1; defer the rest.
- **E7 — Mobile UX.** Full-screen/touch/safe-area layout, engine-status banners on small screens, permission
  flows, background/foreground audio behavior.
- **E8 — Distribution & CI.** Use existing signing certs to produce an installable build for the iPad; wire the
  **iOS build into the macOS CI runner** so the target can't silently rot.

---

## 6. Suggested sequencing (milestones)

1. **M1 Spike & decision** (E1): engine go/no-go + build recipe. *Gate: do the Rust crates target iOS?*
2. **M2 Walking skeleton** (E2 + E3): stub-engine app on the iPad; iOS target builds in CI.
3. **M3 Chat on device** (E4): Metal Qwen3-4B, memory entitlements, streamed tokens.
4. **M4 Voice on device** (E5): STT + TTS from the iPad.
5. **M5 In-process agent** (E6): MVP Rust agent on-device (can run partly parallel once M2 lands).
6. **M6 Polish + ship** (E7 + E8): mobile UX, CI iOS build, installable on Josh's iPad.

Spine: **M1 → M2 → M3 → M4 → M6**, with **M5 (agent)** parallelizable after M2 and E8 setup startable early.

---

## 7. Risks & open questions

- **R1 (highest):** `llama-cpp-sys-2`/`whisper-rs` may not build for iOS without patches. M1 resolves; no-go
  changes the LLM/STT strategy.
- **R2 (large scope):** the **in-process Rust agent** (E6) is a real rewrite of ORBIS's Python; needs its own
  discovery + PRD. Risk of v1 bloat — recommend a deliberately minimal agent MVP, rest deferred.
- **R3:** WKWebView mic stops in background; per-call permission popup is a UX wrinkle.
- **R4:** an app that runs a local server + downloads a multi-GB model — keep the download as *data* in the
  container; document for any review.
- **Q1:** What is the **minimum viable agent** for iPad v1 (what must the in-process Rust agent actually do)?
  Needs a short follow-up brief once E6 discovery reads `~/dev/ORBIS`.

## 8. Out of scope (v1)
- Desktop as a shipped product; Kokoro TTS on iPad; CUDA; older/low-RAM iPads; iPhone form factor;
  full ORBIS agent parity (only the MVP agent ships v1).

## 9. Non-negotiables
- **No GPL** in the shipped binary (rules out espeak-ng/Kokoro v1).
- **No subprocess / no downloaded executable code** (everything in-process / linked).
- **iPad-first**: every decision optimizes for the M4 iPad Pro, not cross-platform parity.
