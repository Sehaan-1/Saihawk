"""
selectors.py  — Internshala DOM Selector Registry  [v2]
=========================================================
Single source of truth for every CSS selector used by the
Internshala scraper, submitter, and login agents.

If Internshala updates its frontend, ONLY this file needs to change.

Versioning strategy:
    Primary selectors are the most reliable.
    Fallback selectors are tried in order if the primary fails.
    The scraper uses _first_visible() helpers to try each in sequence.
"""

# ---------------------------------------------------------------------------
# Flat selector map  (primary selectors used by the Playwright locators)
# ---------------------------------------------------------------------------

SELECTORS: dict[str, str] = {

    # ----------------------------------------------------------------
    # Listing / Search Results Page
    # ----------------------------------------------------------------

    # Container wrapping one job card on the results listing
    "job_card"                : ".internship_meta",

    # ----------------------------------------------------------------
    # Login Page
    # ----------------------------------------------------------------

    "login_email"             : "#email",
    "login_password"          : "#password",
    "login_submit"            : "#login_submit",

    # Element visible after a successful login (used to confirm auth)
    "login_success_indicator" : ".profile_container",

    # ----------------------------------------------------------------
    # Individual Job / Internship Posting Page
    # ----------------------------------------------------------------

    # Job title — multiple selectors observed across posting types
    "job_title"               : "h1.heading_4_5.profile",

    # Company name
    "company_name"            : "a.link_display_like_text",

    # Mandatory skills badges
    "skills_container"        : ".round_tabs",

    # Stipend / compensation display
    "stipend"                 : ".stipend_salary",

    # Apply button  — varies between internship and full-time job pages
    "apply_button"            : "#apply_now_button",

    # Shown when the candidate has already applied
    "already_applied_indicator": ".already_applied",

    # ----------------------------------------------------------------
    # Application Wizard (opened after clicking Apply)
    # ----------------------------------------------------------------

    # Each question label in the "Additional questions" section
    "assessment_questions"    : ".form_group .assessment_question label,"
                                ".form-group.additional_question label",

    # Text area inputs corresponding to each question
    "text_area_inputs"        : ".form_group .assessment_question textarea,"
                                ".form-group.additional_question textarea",

    # The final "Submit Application" button
    "submit_final"            : "#submit_application",

    # ----------------------------------------------------------------
    # Pagination
    # ----------------------------------------------------------------

    "next_page_button"        : ".next_page a",
}


# ---------------------------------------------------------------------------
# Fallback selector chains
# (tried in order by the scraper's _first_matching helper)
# ---------------------------------------------------------------------------

FALLBACK_SELECTORS: dict[str, list[str]] = {

    "job_title": [
        "h1.heading_4_5.profile",
        ".heading_4_5.profile",
        ".profile_on_detail_page",
        "h1.job-internship-name",
        "h1",
    ],

    "company_name": [
        "a.link_display_like_text",
        ".heading_6.company_name",
        ".company-name",
        ".company_name a",
    ],

    "apply_button": [
        "#apply_now_button",
        ".btn-apply",
        "button:has-text('Apply now')",
        "a:has-text('Apply now')",
        "#easy_apply_button",
    ],

    "already_applied_indicator": [
        ".already_applied",
        ".already_applied_text",
        "span:has-text('Applied')",
        ".applied-banner",
    ],

    "assessment_questions": [
        ".form_group .assessment_question label",
        ".form-group.additional_question label",
        ".cover_letter_heading",
        "label.question-label",
    ],

    "text_area_inputs": [
        ".form_group .assessment_question textarea",
        ".form-group.additional_question textarea",
        "textarea.form-control",
    ],

    "skills_container": [
        ".round_tabs",
        ".skills-container span",
        ".skill-tags .tag",
    ],

    "stipend": [
        ".stipend_salary",
        ".stipend",
        ".salary span",
    ],
}
