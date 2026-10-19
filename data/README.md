# Your data

This folder is bind-mounted into the container at `/app/data`, so what you put here survives rebuilds.

On the first container start, every `*.example.*` file is copied to its real name (for example
`personal_info.example.yaml` → `personal_info.yaml`). Existing files are never overwritten.
Fill in the real files; git ignores them, so your personal details never get committed.

| File | What it holds |
|------|---------------|
| `personal_info.yaml` | Name, contact, home address, work authorization, education, EEO answers |
| `master_profile.yaml` | All experience, projects and skills the resume can draw from |
| `qa_bank.yaml` | Answers to common application questions, with optional agent instructions |
| `preferences.yaml` | Locations, relocation, salary, seniority, exclusions, page limits |
| `target_roles.yaml` | Roles to find on careers pages (regex patterns + descriptions) |
| `targets.csv` | Companies to apply to, with optional referral contacts (`referral_consent=yes` required) |
| `templates/resume.tex`, `templates/cover_letter.tex` | Your LaTeX templates |
