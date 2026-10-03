---
name: plan-my-day
version: 1.0.0
description: Turn the user's tasks, appointments and energy level into a realistic plan for today. Use when the user asks to plan their day, organize their to-do list, or decide what to do first.
author: Piyo AI
license: MIT
requires:
  tools: []
---

# Plan my day

1. Call `current_time` so the plan starts from the real time of day and the right weekday.
2. If the user has not listed their tasks, fixed appointments and how much time or energy they have, ask for
   those in one short message. Do not guess at appointments.
3. Put fixed appointments in first, then the most important task in the user's best-energy slot, then
   the rest. Leave gaps for meals and breaks; do not schedule every minute.
4. Say what you left out and why if it does not all fit. Offer to move it to tomorrow.
5. Reply with a short timed list, no longer than the user's own input.
