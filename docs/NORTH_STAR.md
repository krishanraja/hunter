# North Star

Status: Current
Owner: Krish Raja
Last verified: 2026-10-18

> By 18 November 2026, Hunter surfaces only roles that are literally the mission with a salary, and every CV and cover letter it builds clears the presentation bar on the first pass. No gate failure reaches Krish, and he would send every package unedited.

Set by Krish on 18 October 2026. It is not reached yet. It is what every session in this repository works toward.

## The number

**Roles a week that are literally the mission with a salary, each packaged to pass the presentation bar first time**: 10 by 16 November 2026, on a linear weekly ladder. Checkpoints: 16 Nov 2026: 10.

```yaml north-star-metric
metric: "Roles a week that are literally the mission with a salary, each packaged to pass the presentation bar first time"
unit: count
direction: up
target: 10
deadline: 2026-11-16
ladder_shape: linear
source: "The Pipeline sheet and the gate results"
status: active
ladder:
  - [2026-10-26, 3]
  - [2026-11-02, 5]
  - [2026-11-09, 8]
  - [2026-11-16, 10]
```

Urgency (U1 to U5) is worked out every week from where the number stands against that week's target, by [the harness contract](https://github.com/krishanraja/ai-harness/blob/main/contract/north-star-metric.md). Set by Krish on 23 October 2026.

## How to use it

- Read it before any work, every session.
- Before you start, say which part of it the task moves. If the task moves nothing toward it, say so first and let Krish decide.
- Prefer the work that moves it furthest, and close each session by noting what moved.
- Only Krish changes this file. Record his ruling in the commit body as `Ruling (Krish, YYYY-MM-DD): ...`.
- When it is reached, say so and ask Krish for the next one.
