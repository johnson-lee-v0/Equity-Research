# Task model routing

The current defaults divide work by task instead of applying maximum reasoning
to every call. The app runs locally and calls models through the authenticated
Codex CLI using your Codex service; an OpenAI API key is not required.

| Work | Default |
| --- | --- |
| Routing, discovery, extraction and monitoring (A00/A01/A02/A06/A08) | GPT-6 Luna, High |
| Fundamental research (A03) | GPT-6 Sol, High |
| Technical, entry, simulation, macro and portfolio reasoning (A04/A05/A07/A09/A10) | GPT-6 Sol, Medium |
| Final five-question decision (A11) | GPT-6 Astra, Medium |

Code continues to own pricing calculations, source checks and validation.
`backend/app/agents/model_policy.py` is the default assignment table. Execution
and cache snapshots use the same assignment; changing the model does not reuse
an answer generated under a different policy. Disabled policies are ignored.
Explicit compatible overrides can request more reasoning. The final decision
contract requires Astra at Medium or higher, and Reddit intake requires Luna
at High or higher. Incompatible overrides produce a visible contract block.

A versioned migration updates the obsolete built-in Luna 5.6 Max / Astra Ultra
defaults while preserving other custom policies and historical run records.
No previous attempt's saved model identity or result is rewritten.

The app honors `ROAD2M_CODEX_BINARY` when explicitly configured. Otherwise it
prefers the installed desktop app's bundled CLI, then a standalone CLI on PATH.
On this machine the standalone CLI was 0.153.4 and rejected GPT-6 Luna/Sol.
The existing desktop bundle, 0.158.0-alpha.2, successfully completed real
structured-output probes for Luna High, Sol High and Astra Medium on September
26, 2026. This changes the app's executable selection, not the user's global
CLI installation. An unsupported-model error is not automatically retried as
a transient network failure.

Role selection follows the current [official model guidance](https://learn.chatgpt.com/docs/models).
The [official changelog](https://learn.chatgpt.com/docs/changelog) records
GPT-6 Luna/Sol support in CLI 0.157.0. Model availability is confirmed by actual
execution; a cached model name alone is not sufficient.
