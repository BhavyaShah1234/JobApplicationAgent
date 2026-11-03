# Your data

This folder is bind-mounted into the container at `/app/data`, so what you put here survives rebuilds.

On the first container start, every `*.example.*` file is copied to its real name (for example
`applicant.example.yaml` → `applicant.yaml`). Existing files are never overwritten.
Fill in the real files; git ignores them, so your personal details never get committed.

| File | What it holds |
|------|---------------|
| `applicant.yaml` | Every answer for application forms in one place: the questions real portals ask (with their wordings), your answers, instructions and lookups for the agent, and answers you gave on the spot (`learned`) |
| `master_profile.yaml` | All experience, projects and skills the resume can draw from |
| `preferences.yaml` | Job search: locations, seniority, employment types, exclusions, page limits |
| `target_roles.yaml` | Roles to find on careers pages (regex patterns + descriptions) |
| `targets.csv` | Companies to apply to, with optional referral contacts (`referral_consent=yes` required) |
| `templates/resume.tex`, `templates/cover_letter.tex` | Your LaTeX templates |

`applicant.yaml` replaces the older `personal_info.yaml` and `qa_bank.yaml` (and the relocation / salary parts of
`preferences.yaml`); those are no longer read for forms and can be deleted.
