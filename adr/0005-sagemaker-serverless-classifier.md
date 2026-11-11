# 0005. Trained classifier on SageMaker Serverless as tier 3

- **Status:** Accepted (2026-10-02)
- **Date:** 2026-10-02
- **Spec:** [../specs/sagemaker-classifier/](../specs/sagemaker-classifier/)
- **Related:** 0001, 0003, 0004

## Context

Some class pairs defeat both cheaper learned tiers:

- **Successive editions of one form** share nearly all their boilerplate (which
  defeats tier 1) and all their meaning (which defeats tier 2).
- **Parent and variant forms**, such as a return and its amended version, differ
  in a few words.

Separating them takes a model that learns from many labelled examples, and
ideally one that sees layout as well as text. Such a model must be hosted
somewhere. Traffic reaching tier 3 is a small, bursty fraction of total volume,
so an always-on instance would sit idle most of the time.

## Decision

1. **Tier 3 is a fine-tuned classifier hosted on SageMaker Serverless
   Inference.** Charges accrue only while a request is running.
2. **The model must fit serverless limits:** CPU only and at most 6 GB of
   memory, so a distilled transformer encoder (text, or text + layout where
   bounding boxes are available).
3. **Endpoint contract:** request `{text, pages?, model_version?}`, response
   `{probabilities: {class_id: p}, model_version}`. The endpoint returns
   probabilities and **never decides acceptance**. Thresholds `τ₃` / `δ₃`
   are applied by the calling tier, alongside the other tiers' thresholds
   (ADR-0001 §6).
4. **Endpoint errors, timeouts and throttling mean the tier abstains** with a
   reason. The document becomes `UNKNOWN`; it is never failed, and never given
   a guess.
5. **Gated adoption.** Tier 3 is enabled only for the class families where an
   evaluation shows tiers 1 and 2 miss the target precision. Every other class
   never reaches it.
6. **Models are versioned in a model registry.** Promotion requires the
   evaluation report and a threshold sweep on the held-out set.

## Alternatives considered

| Alternative | Why it lost |
|---|---|
| Real-time SageMaker endpoint | Pays for idle instances to serve a small, bursty share of traffic. Revisit if the latency target rules out cold starts |
| A large LLM prompted to classify | Higher cost per call, no calibrated output, and less reliable than a fine-tuned model on fine-grained distinctions between editions |
| Amazon Comprehend custom classification | A viable managed option. It lost because its training and inference are opaque, it offers no layout input, and its pricing does not suit sporadic traffic. Reconsider if there is no team to own model training |
| Container in a function runtime | Transformer weights and runtimes exceed comfortable function package and memory sizes, and cold starts are worse |
| No tier 3: send look-alikes to human review | Acceptable while look-alike volume is low. That is exactly why adoption is gated (decision 5) |

## Consequences

- **Positive.** The highest accuracy ceiling, applied only where it is needed.
  No idle instance cost.
- **Negative.** Brings in a training pipeline, labelled-data requirements (in
  the hundreds per class), a model registry and an endpoint to operate. Cold
  starts can take seconds, which is acceptable only for asynchronous
  processing. Per-endpoint concurrency is capped, so bursts can throttle,
  causing more abstentions (decision 4).
- **Neutral.** The labelled data gathered for this tier also improves tiers 1
  and 2.

## AWS integration

- **Endpoint:** a SageMaker Serverless endpoint, with `MemorySizeInMB` and
  `MaxConcurrency` set explicitly (SMKR-AC-21).
- **Invocation:** `sagemaker:InvokeEndpoint`, scoped to the endpoint ARN.
- **Training:** a SageMaker Training Job (Hugging Face container), registered
  in a Model Registry group and gated by the promotion check in
  `training/sagemaker/promote.py`.

## Revisit when

- An interactive latency target appears: use provisioned concurrency or a
  real-time endpoint.
- The look-alike classes become a large share of traffic: compare serverless
  cost against a real-time instance.
- Tier 1 and 2 accuracy on the gated families reaches target: retire tier 3.
