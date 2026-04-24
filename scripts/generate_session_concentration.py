"""
Generate sample session concentration data for multiple users.
Creates user folders with 90 concentration JSON files each for 6 users.
"""

import json
import os
import uuid
from datetime import datetime, timedelta
import random
from pathlib import Path

def generate_session_data(user_num, session_num):
    """Generate a single session concentration data with high variety."""
    # Random parameters for realistic variation
    duration = random.randint(30, 600)  # 30 seconds to 10 minutes
    audio_db = round(random.uniform(30, 85), 1)
    spike_db = audio_db + random.randint(5, 25)
    stabilized_db = audio_db - random.randint(2, 12)
    speech_confidence = round(random.uniform(0.4, 0.95), 2)
    has_speech = random.random() > 0.4
    gps_initial = round(random.uniform(2.5, 5.5), 1)
    gps_final = round(random.uniform(1.0, 3.5), 1)
    concentration_score = random.randint(4, 10)
    motion_stable = random.random() > 0.35
    has_hr_data = random.random() > 0.25
    
    # ===== PHASE 1: Audio Analysis (Multiple variants) =====
    audio_variants = [
        f"Audio recording captures initial spike of {spike_db} dBA, gradually settling to {stabilized_db}-{audio_db} dBA midway through session.",
        f"Ambient noise levels show fluctuation between {stabilized_db} and {spike_db} dBA with {random.choice(['consistent', 'sporadic', 'intermittent'])} patterns.",
        f"Acoustic analysis reveals baseline of {stabilized_db} dBA with periodic spikes up to {spike_db} dBA throughout the {duration}s recording.",
        f"Audio environment demonstrates {random.choice(['steady', 'variable', 'unpredictable'])}" f" noise signature ranging {stabilized_db}-{spike_db} dBA.",
        f"Sound level monitoring shows {random.choice(['gradual decline', 'stabilization', 'fluctuation'])}" f" from {spike_db} dBA to sustained {audio_db} dBA.",
    ]
    audio_base = random.choice(audio_variants)
    
    speech_addon = ""
    if has_speech:
        speech_variants = [
            f" Speech event detected at {random.randint(1, int(duration))}s mark with confidence {speech_confidence}.",
            f" Human voice detected with {speech_confidence} confidence, likely {'nearby conversation' if audio_db > 60 else 'adjacent discussion'}.",
            f" Vocalization detected mid-session (confidence: {speech_confidence}), {'contributing to' if audio_db > 65 else 'not significantly affecting'} ambient baseline.",
            f" One or more speech segments identified (avg confidence {speech_confidence}), suggesting {'social environment' if audio_db > 65 else 'quiet setting with occasional voices'}.",
        ]
        speech_addon = random.choice(speech_variants)
    
    noise_context = ""
    if audio_db < 50:
        noise_context = " Overall environment is exceptionally quiet, highly conducive to concentration."
    elif audio_db < 60:
        noise_context = " This quiet setting is favorable for sustained focus and deep work."
    elif audio_db < 70:
        noise_context = " Moderate noise levels present; some distraction possible but manageable."
    elif audio_db < 80:
        noise_context = " Higher ambient noise detected; concentration requires active focus effort."
    else:
        noise_context = " Very loud environment detected; concentration significantly compromised."
    
    audio_analysis = audio_base + speech_addon + noise_context
    
    # ===== PHASE 1: Vitals Analysis (Multiple variants) =====
    if has_hr_data:
        hr = random.randint(55, 95)
        hr_status = ""
        if hr < 60:
            hr_status = "unusually low resting rate, possibly indicating relaxation or fatigue"
        elif hr < 70:
            hr_status = "baseline typical for calm, focused state"
        elif hr < 80:
            hr_status = "elevated but acceptable for engaged concentration"
        elif hr <= 85:
            hr_status = "moderately elevated, consistent with active cognitive engagement"
        else:
            hr_status = "consistently elevated, suggesting heightened stress or engagement"
        
        vitals_variants = [
            f"Cardiovascular data shows average heart rate of {hr} bpm ({hr_status}). Consistent rate suggests {random.choice(['stable mental state', 'maintained focus', 'steady engagement', 'regulated stress levels'])}.",
            f"Heart rate averaged {hr} bpm throughout session ({hr_status}). Data indicates user was in {'optimal' if 60 <= hr <= 80 else 'suboptimal'} physiological state for concentration.",
            f"Resting heart rate around {hr} bpm observed, reflecting {random.choice(['calm demeanor', 'engaged but controlled pace', 'relaxed yet focused', 'measured attention'])}" f". {random.choice(['Minimal fluctuation detected.', 'Rate remained stable.', 'Consistent throughout.'])}",
            f"Vitals monitoring recorded {hr} bpm as session average. This {'aligns with' if 65 <= hr <= 85 else 'deviates from'} ideal focus-state parameters.",
        ]
        vitals_text = random.choice(vitals_variants)
    else:
        vitals_variants = [
            f"No heart rate data collected during this {duration}s session. Physiological stress assessment unavailable.",
            f"Cardiovascular monitoring absent for this {duration}-second recording. Unable to correlate physical state with concentration.",
            f"Insufficient biometric data (no HR samples) limits assessment of user's physiological condition during {duration}s session.",
            f"Heart rate telemetry not available for this session period. Cannot evaluate stress-induced arousal state.",
        ]
        vitals_text = random.choice(vitals_variants)
    
    # ===== PHASE 1: GPS and Motion Analysis (Multiple variants) =====
    gps_variants = [
        f"GPS positioning shows initial variance of {gps_initial}m, refining to {gps_final}m accuracy over session duration. ",
        f"Geolocation data exhibits high initial uncertainty ({gps_initial}m) that improves to {gps_final}m as session progresses. ",
        f"GPS signal strength inconsistent at start ({gps_initial}m error margin), achieving {gps_final}m lock after stabilization. ",
        f"Location services recorded {gps_initial}m initial error that converges to {gps_final}m by session midpoint. ",
    ]
    gps_base = random.choice(gps_variants)
    
    motion_variants_stable = [
        "Motion data reflects immediate settling into stationary posture, maintained consistently.",
        "Accelerometer readings show user quickly becoming immobile and remaining seated throughout.",
        "Movement sensors indicate rapid transition to stillness; no significant repositioning detected.",
        "Kinetic data shows user establishing stable position within first seconds; minimal body movement thereafter.",
    ]
    motion_variants_active = [
        "Motion activity scattered throughout session with user frequently shifting position.",
        "Accelerometer detects ongoing adjustments and repositioning throughout the recording.",
        "Movement remains variable with user changing posture multiple times during session.",
        "Kinetic data shows sustained activity with no prolonged stationary periods.",
    ]
    
    motion_text = random.choice(motion_variants_stable if motion_stable else motion_variants_active)
    gps_analysis = gps_base + motion_text
    
    # ===== PHASE 2: Correlations (Multiple variants) =====
    correlation_variants = [
        f"GPS stabilization timing {'aligns closely with' if motion_stable else 'precedes'} motion state change. Audio context {'remains stable' if audio_db < 70 else 'shows variation'} throughout captured period.",
        f"Positional settling (GPS convergence) {'corresponds to' if motion_stable else 'diverges from'} kinetic patterns. Acoustic {'consistency' if audio_db < 65 else 'fluctuation'} observed.",
        f"The shift toward {'sustained stillness' if motion_stable else 'continued motion'} aligns with GPS signal stabilization. Audio channel demonstrates {'minimal variance' if audio_db < 60 else 'notable transitions'}.",
        f"Correlation analysis reveals {'strong alignment' if motion_stable and audio_db < 70 else 'moderate compatibility'} between positional, kinetic, and acoustic factors.",
    ]
    correlations = random.choice(correlation_variants)
    
    # ===== PHASE 2: Holistic Assessment (Multiple variants) =====
    if concentration_score >= 8:
        holistic_variants = [
            f"Session exemplifies near-optimal conditions: minimal acoustic disturbance ({audio_db} dB), {'stable' if motion_stable else 'variable'} positioning, and {'strong' if has_hr_data else 'unmeasured'} focus indicators. Duration ({duration}s) {'sufficient' if duration > 300 else 'borderline'} for sustained concentration assessment.",
            f"Analysis indicates excellent concentration potential: quiet audio environment ({audio_db} dB), {'settled' if motion_stable else 'dynamic'} posture, and {'favorable' if has_hr_data and 60 <= int(random.uniform(65, 85)) <= 85 else 'unmeasured'} physiology.",
            f"Session demonstrates strong focus-supportive conditions with low ambient noise ({audio_db} dB), {'minimal' if motion_stable else 'frequent'} body repositioning.",
        ]
    elif concentration_score >= 6:
        holistic_variants = [
            f"Session shows moderate concentration quality with mixed environmental factors. Acoustic baseline of {audio_db} dB provides {'acceptable' if audio_db < 70 else 'challenging'} conditions. User maintained {'consistent' if motion_stable else 'variable'} positioning.",
            f"Moderate focus conditions evident: audio levels at {audio_db} dB, {'stable' if motion_stable else 'dynamic'} user state. Session duration ({duration}s) {'allows' if duration > 300 else 'limits'} comprehensive concentration evaluation.",
            f"Analysis suggests reasonable concentration potential with {audio_db} dB acoustic environment and {'settled' if motion_stable else 'mobile'} user behavior.",
        ]
    else:
        holistic_variants = [
            f"Session presents suboptimal focus conditions: elevated ambient noise ({audio_db} dB), {'unstable' if not motion_stable else 'transitional'} positioning. Concentration likely impacted.",
            f"Challenging environmental factors identified: {audio_db} dB acoustic baseline, {'frequent motion' if not motion_stable else 'postural adjustments'}, potential physiological stress.",
            f"Session analysis reveals concentration impediments: noisy setting ({audio_db} dB), {'restless' if not motion_stable else 'unsettled'} user state.",
        ]
    holistic = random.choice(holistic_variants)
    
    # ===== PHASE 3: Recommendations (Multiple variants) =====
    recommendations = []
    
    if audio_db > 75:
        audio_recs = [
            "Invest in noise-canceling technology to mitigate high ambient levels.",
            "Seek quieter environment immediately—current >75dB threshold severely impacts focus.",
            "Relocate to acoustically isolated space to improve concentration capacity.",
            "Use active noise cancellation or white noise to counteract loud surroundings.",
        ]
        recommendations.append(random.choice(audio_recs))
    elif audio_db > 65:
        audio_recs = [
            "Consider earplugs or noise-reducing headphones for moderate ambient levels.",
            "Modest noise control (e.g., ambient music, quiet background) could improve focus.",
            "Attempt sessions in slightly quieter locations to evaluate impact on concentration.",
        ]
        recommendations.append(random.choice(audio_recs))
    
    if duration < 120:
        duration_recs = [
            f"Extend session length beyond {duration}s to build sustained focus momentum.",
            f"Current {duration}-second duration too brief for meaningful concentration assessment; aim for 10+ min sessions.",
            f"Gradually increase session length from current {duration}s to develop deeper concentration habits.",
        ]
        recommendations.append(random.choice(duration_recs))
    elif duration < 300:
        duration_recs = [
            f"Current {duration}s sessions reasonable; consider testing longer 30-min blocks.",
            f"Moderate session length adequate; potential to extend to {int(duration * 1.5)}s for skill building.",
        ]
        recommendations.append(random.choice(duration_recs))
    
    if not motion_stable:
        motion_recs = [
            "Establish dedicated seating to minimize postural shifts and maintain focus.",
            "Practice deliberate stillness; reduce repositioning frequency during study periods.",
            "Invest in ergonomic furniture to reduce fidgeting and increase stationary time.",
            "Address underlying restlessness through movement breaks between sessions.",
        ]
        recommendations.append(random.choice(motion_recs))
    
    if not recommendations:
        recommendations = [
            f"Current session conditions favorable for concentration. Maintain established patterns.",
            f"Existing environment well-suited for focused work. Build on current successful setup.",
            f"Session demonstrates effective concentration habits. Continue present approach.",
        ]
    
    recommendations_text = " ".join(recommendations[:2]) if len(recommendations) >= 2 else recommendations[0]
    
    # ===== Reason (Multiple variants) =====
    reason_variants = [
        f"Score weighted toward audio environment ({audio_db}dB—{'quiet' if audio_db < 60 else 'moderate' if audio_db < 75 else 'loud'}), {'stable' if motion_stable else 'variable'} positioning, and session duration ({duration}s). Overall {'favorable' if concentration_score >= 7 else 'mixed' if concentration_score >= 5 else 'challenging'} conditions.",
        f"Concentration score reflects {['poor', 'suboptimal', 'moderate', 'good', 'excellent'][int((concentration_score - 4) / 1.5)]} audio conditions ({audio_db}dB), {'minimal' if motion_stable else 'frequent'} motion, and {'adequate' if duration > 300 else 'brief'} session length.",
        f"Score determined by comprehensive assessment: ambient noise management ({audio_db}dB), user-state stability ({'settled' if motion_stable else 'active'}), physiological data ({'available' if has_hr_data else 'absent'}), and sustainable duration ({duration}s).",
    ]
    reason = random.choice(reason_variants)
    
    session_data = {
        "phase_1": {
            "audio": audio_analysis,
            "vitals": vitals_text,
            "gps_motion": gps_analysis
        },
        "phase_2": {
            "correlations": correlations,
            "holistic_assessment": holistic
        },
        "phase_3": {
            "recommendations": recommendations_text
        },
        "score": concentration_score,
        "reason": reason
    }
    
    return session_data

def main():
    """Generate all session concentration data."""
    base_output_dir = Path(__file__).parent.parent / "llm" / "CCoT" / "output"
    seen_payloads = set()
    
    # Create output directory if it doesn't exist
    base_output_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate data for 6 users
    for user_num in range(1, 7):
        user_folder = base_output_dir / f"user-{user_num}"
        user_folder.mkdir(exist_ok=True)
        
        print(f"Generating data for user-{user_num}...")
        
        # Generate 90 sessions for each user
        for session_num in range(1, 91):
            # Regenerate on collision to guarantee unique payloads across all 540 files.
            while True:
                session_data = generate_session_data(user_num, session_num)
                payload_key = json.dumps(session_data, sort_keys=True)
                if payload_key not in seen_payloads:
                    seen_payloads.add(payload_key)
                    break
            
            # Create filename
            filename = f"session-{session_num:03d}_concentration.json"
            filepath = user_folder / filename
            
            # Write JSON file
            with open(filepath, "w") as f:
                json.dump(session_data, f, indent=2)
            
            if session_num % 10 == 0:
                print(f"  ✓ Generated {session_num}/90 sessions")
        
        print(f"✓ Completed user-{user_num}\n")
    
    print("=" * 50)
    print("Data generation complete!")
    print(f"Generated 6 users × 90 sessions = 540 files")
    print(f"Unique payload count: {len(seen_payloads)}")
    print(f"Location: {base_output_dir}")

if __name__ == "__main__":
    main()
