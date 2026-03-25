# Minimal User Reflection Form — 7 Questions Only

**Time to complete:** 2 minutes | **When:** Immediately after session ends

---

## THE PRINCIPLE

**Each question directly changes how we interpret your sensor data.** Answer only what's true; skip if uncertain.

---

## Q1: FOCUS SELF-RATING 
*(Changes baseline interpretation)*

**How focused did you feel overall IN THIS SESSION?**
- Scale: 1 (completely distracted) → 10 (perfectly focused)

**Why it matters:**
- Sensors don't measure *feeling*. If model says 6.5 but you felt 4, we know the model's baseline is wrong for you today.
- This teaches us what sensor patterns mean for *you specifically*.

---

## Q2: COMPARED TO MODEL 
*(Reveals systematic model bias for this user)*

**The model measured your focus at [MODEL_SCORE]. Your feeling was:**
- [ ] MORE focused than the score suggests
- [ ] About the same
- [ ] LESS focused than the score suggests

**Why it matters:**
- If you always feel lower focus than sensors suggest → sensors might be over-counting motion/activity.
- If always higher → you might mask low focus with surface engagement.
- **This calibrates the model per user.**

---

## Q3: PEAK STRESS LEVEL 
*(Reinterprets physiology: heart rate, movement patterns)*

**What was your HIGHEST stress level during this session?**
- Scale: 1 (relaxed) → 10 (extremely stressed)

**Why it matters for sensors:**
- **Heart rate:** Elevated HR could mean focus-loss OR stress carryover. Knowing stress lets us distinguish them.
- **Motion:** Less movement could mean deep focus OR stress-induced freezing.
- **Result:** Same sensor data, completely different interpretation.

*Example: Model sees elevated heart rate. If you say stress=8, that HR is stress-noise. If stress=2, that HR means attention dropped.*

---

## Q4: ENVIRONMENT QUALITY 
*(Recalibrates audio threshold)*

**What was your environment like? (Give best estimate)**

**Noise level:**
- Quiet (under 50dB)
- Moderate (50-70dB)
- Noisy (70-85dB)
- Very Noisy (85+ dB)

**Location:**
- Home / Office (private) / Office (shared) / Library / Café / Other

**Why it matters for sensors:**
- **Microphone:** If you say "very noisy 85+dB", that same audio reading is normal background, not a distraction.
- **Motion:** Noisy environment = more involuntary fidgeting = motion sensor triggers false alarms we now ignore.
- **Result:** Audio threshold is context-dependent per user per environment.

*Example: Model flags an audio spike. In quiet library → red flag. In café → just background.*

---

## Q5: MAIN DISRUPTION TYPE 
*(Marks sensor anomalies as external vs internal)*

**What was the BIGGEST thing that disrupted your focus?**
- [ ] Emotional stress / worry / conflict
- [ ] Environmental noise / external sounds
- [ ] External interruption (call, message, notification, person)
- [ ] Your own mind wandering
- [ ] Task was too hard / confusing
- [ ] Physical: hunger / fatigue / discomfort
- [ ] No major disruption

**Why it matters for sensors:**
- **Emotional stress** → Heart rate ↑ (reinterpret as stress, not lost focus)
- **Noise** → Motion ↑ (reinterpret as coping, not distraction)
- **Mind wandering** → Motion ↓, HR stable (reinterpret as dissociation)
- **Task difficulty** → All sensors plateau (reinterpret as effort, not loss)
- **External interruption** → Motion/HR spike + recovery window (mark recovery, don't count as lost focus)

**This is THE most powerful input.** It *labels* what sensor anomalies actually mean.

---

## Q6: TASK DIFFICULTY 
*(Adjusts focus baseline)*

**How difficult was the task?**
- Easy (could do on autopilot)
- Moderate (required focus, felt doable)
- Hard (required deep concentration)
- Very Hard (struggled to understand)

**Why it matters for sensors:**
- Focus score 7 on easy task = LOWER quality focus than 7 on hard task.
- Model can't tell task difficulty from sensors alone.
- Same heart rate pattern = different things: hard task = appropriate effort; easy task = wandering mind.

*Example: Hard algebra → justified HR elevation + stillness. Easy reading → same pattern suggests you lost focus.*

---

## Q7: WHY WAS IT DIFFERENT? 
*(Captures model blind spots)*

**In 1-2 sentences: What did the model miss? Why did you feel different from the score?**

*Examples:*
- *"I was stressed about morning conflict even though I was moving around"*
- *"Took a 10-minute break mid-session which reset my focus but sensors show low motion"*
- *"Task was harder than expected so concentration effort was higher than usual"*

**Why it matters:**
- Captures the one thing no sensor can detect: *context and intention*.
- Your break isn't a loss of focus event; it's a recovery pattern.
- Your stillness isn't mind-wandering; it's deep thinking.

---

## HOW THESE 7 INPUTS RESHAPE SENSOR INTERPRETATION

| Your Answer | Affects This Sensor | How Interpretation Changes | Practical Impact |
|------------|-----------------|----------------------|--------|
| focus_rating < model_score | Heart Rate, Motion | Model's baseline is skewed high for you | Next time: expect lower HR/motion for same actual focus |
| compared_to_model = "LESS" | Baseline calibration | You're a conservative self-rater; adjust upward | Your "5" = actual 6; don't over-interpret as low focus |
| stress_level_peak = 8 | Heart Rate, Breathing | HR elevation is stress-carryover, NOT lost focus | Don't flag tachycardia as attention dropped |
| environment = "very noisy" | Microphone, Motion | Ambient noise is normal; motion is coping behavior | Ignore audio spikes; raise motion threshold by 30% |
| disruption = "external interrupt" | All sensors | That time window is recovery period, not loss | Don't count interrupt period in focus score; measure recovery speed |
| task_difficulty = "HARD" | Focus baseline | This user's "6.5" on hard task = actual focused effort, not inflated | Don't recommend "focus harder"; task is appropriately challenging |
| why_different = "took break" | Motion pattern | Motion drop isn't loss of focus; it's intentional reset | Identify recovery intervention; mark as positive, not negative |

---

## WHAT TO IGNORE (Not Sensor-Actionable)

❌ Exact mind-wandering frequency (you don't remember minute-by-minute)
❌ Which intervention was "most helpful" (you don't know yet; that's what we're learning)
❌ Physical energy level separate from focus (not a sensor signal)
❌ Confidence in self-rating (adds interpretation noise)
❌ Session trajectory details (too much recall burden)

---

## INTEGRATION WITH analyst.py

The `analyst.py` `extract_user_reflection()` already handles this schema:

```json
{
  "user_reflection": {
    "overall_focus_rating": 5.0,
    "compared_to_model_result": "LOWER",
    "stress_level_peak": 7,
    "environment_quality": "NOISY",
    "noise_level_estimated_db": 75,
    "main_disruption": "emotional_stress_carryover",
    "task_difficulty": "HARD",
    "why_different": "..."
  }
}
```

Each field flows directly into the LLM prompts for:
- **discrepancy_reasoning**: Why did the model and user disagree?
- **personalization_profile**: What does this teach us about interpreting sensors for this user?
