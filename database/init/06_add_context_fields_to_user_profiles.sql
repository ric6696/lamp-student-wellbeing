-- Add context average concentration score fields to user_personalization_profiles
ALTER TABLE user_personalization_profiles
ADD COLUMN IF NOT EXISTS mental_readiness_averages JSONB,
ADD COLUMN IF NOT EXISTS activity_context_averages JSONB,
ADD COLUMN IF NOT EXISTS environment_context_averages JSONB;

-- Update the updated_at column for any rows that receive these new fields
UPDATE user_personalization_profiles
SET updated_at = NOW()
WHERE mental_context_averages IS NULL
  AND activity_context_averages IS NULL
  AND environment_context_averages IS NULL;
