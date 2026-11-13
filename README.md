# JobApplicationAgent
This is a harness and agent pair that fill job applications on my behalf.

For the current Docker-to-desktop application fallback, see
[Desktop application fallback](docs/desktop-applications.md). It supports public HTTPS
application URLs across employers and uses the shared Voyager form-filling workflow.

The harness is the browser, LaTex compiler, persistent storage of documents, credentials, agent experience/notes, scratchpade, etc.
Trigger to start a job application filling is either pasting a URL of the opening, mentioning a company/organization name or mentioning multiple company/organization names either as a CSV or Excel sheet via path or comma-separated string.

Agent responsibilities:
Global default action (runs everytime before a trigger event arrives) as `SystemMessage`:
1. Check if global email, browser profile, LinkedIn account (unverified is fine), cookies and other browser runtime artifacts exists
- If global email doesn't exist:
  1. Create an email (preferrably Gmail)
  2. Save credentials in stateful location so its information persists even after the Docker container (environment) is exited, deleted or rebooted or a new trigger event arrives.
  3. Log into browser profile (preferrably Google Chrome or Chromium for headful and headless executions) via global email created in step 1
  4. Set up cookies and browser runtime artifacts that make it look like a human is using the browser (deleting the agent's scent)

- If browser profile doesn't exist (but global email does):
  1. Log into browser (preferrably Google Chrome or Chromium for headful and headless executions) using global email
  2. Set up cookies and browser runtime artifacts that make it look like a human is using the browser, thus deleting the agent's scent)

- If LinkedIn profile doesn't exist (but global email and browser profile does):
  1. Sign up for an account using global email address
  2. Get OPT or verification link from inbox of global email
  3. Log into the account
  (Can use human-in-the-loop in any step if stuck or want to bypass some security measure like CAPTCHA, Cloudflare or Google verification of LinkedIn that becomes an issue to the agent)

- If cookies or any other browser runtime artifact don't exist (but global email, browser profile and LinkedIn account exist):
  1. Set up cookies and browser runtime artifacts that make it look like a human is using the browser, thus deleting the agent's scent)

Trigger events as `HumanMessage` and general agent actions:
- If URL provided:
  1. Open the URL
  2. Scrape the JD correctly (including requirements, company description, role, pay range, basic and preferred qualification, qualities, what the company is looking for, etc) using knowledge from previous interaction with that exact job portal or considering it a new portal and recording interaction experience
  3. Create a custom resume and cover letter from given template in Latex with existing and required technical skills
  4. Compile the LaTex code in a LaTex compiler
  5. Verify compiled code for syntax and visual correctness
  6. Achieve an average ATS score of > 90% between created resume and JD on 3-5 different ATS websites (use global email if ATS scoring websites ask to sign in)
  7. Repeating steps 3, 4, 5 until condition in step 6 is satisfied
  8. Start application with resume created in step 7 and my saved personal details. (This can include creating a new account via global email, logging into an existing account or direct resume upload on some portals)
  - If credentis don't exist in stateful previous interaction record:
    1. Use the global email address to sign up
    2. Get OTP, code or verification link from the global email's inbox
    3. Log in once verified
    4. Record credentials, company/organization's portal information in stateful previous interaction record
    (Can use human-in-the-loop in any of steps 1, 2, 3 if stuck or want to bypass some security measure like CAPTCHA, Cloudflare or Google verification of the portal that becomes an issue to the agent)
  - Else:
    1. Log in via saved credentials from stateful previous interaction record record
  9. Populate fields with the correct answers mentioned in personal details template and resume. (Can involve navigating multiple page applications)
  10. Submit and waiting for application submission email in inbox of global email

- If company/organization mentioned:
  1. Open the careers page of mentioned organization
  2. Search for all relevant roles
  3. Either save the URLs in an in-memory list or scratchpad (preferred) to apply later or open the job opening URLs in a new tab.
  - If URLs saved in a list:
    1. Iterate over the URLs in the list or scratchpad
    2. Fill the application in the same way as URL-based job application steps
    3. Remove URL from list or mark the URL of the job as applied
  - Else:
    1. Sign up to the company or organization's portal
    2. Once logged in, refresh the tabs containing job openings. You should have logged in to all the tabs and wouldn't need to log in every time while submitting the application

- If CSV or Excel sheet path provided:
  1. Parse through the CSV or Excel sheet. It may contain some company contact (name, email or only company/organization names)
  2. Visit the company's career page/portal
  3. Follow all the steps when company/organization mentioned with an additional modification
  4. When filling out an application, use the company contact as a referral

To perform all these actions, the harness needs the following capabilities:
- Scratchpad
- Browser (headless and headful)
- Web search tool
- Browser interaction tool
- Cookie management in browser
- LaTex compiler
- Filesystem
- Read, write and edit access
- Structured tool and LLM outputs
- Terminal access
- Vision capability
- and anything you can think of
