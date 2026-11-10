# Verification and scoring

## Evidence standards

Use `pass`, `fail`, or `unknown` for each required check. Every asserted pass or
fail must cite an observed source URL and supporting excerpt. Unknown is the
correct result when evidence is missing, inaccessible, ambiguous, or stale.
Do not infer a fact from a search query, a model prediction, or lack of a denial.

Verify these separately:

| Check | Supporting evidence | Insufficient evidence |
|---|---|---|
| Active hiring | Author describes an available role or active team hiring | Generic careers content or an old closed vacancy |
| Full-time | Explicit employment type or linked role description | Absence of the word internship |
| Early-career suitability | New-grad/junior opening, compatible experience requirement, or PhD entry role | A vaguely technical role with no level information |
| US job location | US work location, or remote eligibility explicitly covering the US | Author lives in the US |
| US author location | Current profile location or an explicit current statement | Employer headquarters or university location |
| Management responsibility | Current people/team leadership responsibilities | The word manager alone, a former title, or product/project management without hiring authority |
| Current company affiliation | Current role at the company advertising the job | Past employment, an agency client, or a repost |
| Own-team hiring | Explicit hiring for their team, direct reporting relationship, or corroborating evidence | Being employed at the company and sharing a vacancy |

Recruiters, agency staff, content creators, unrelated employers, and employees
merely sharing openings do not satisfy the hiring-manager requirement. Founders,
heads, directors, and technical leads can qualify when evidence establishes their
actual hiring responsibility. A portfolio-manager title alone is not decisive.

Split distinct advertised roles within a post. Do not classify every role as
junior because the post includes one junior opening. Preserve uncertain cases
for review. Explicit senior-only, internship, non-US, or unrelated-role evidence
excludes the affected role when that filter is enforced.

## Resume and eligibility

Use only the resume variants provided in the batch. Select one variant per role
and record its identifier with supporting excerpts. Do not merge contradictory
publication status, contact details, or achievements from different versions.
Omit conflicting claims from outreach; flag any conflict material to fit.

The local candidate configuration controls availability and sponsorship. For this
user, the initial settings are January 5, 2027 availability and a future
sponsorship requirement, both **flag only**. Missing requirements remain unknown.
Do not turn a potential eligibility conflict into rejection when configured as
advisory. Disabled semantic filters must not silently become exclusion rules.

## Scoring rubric

Use integer component scores within these bounds. The service calculates the
total; do not supply an arbitrary probability of success.

| Component | Maximum | Assessment |
|---|---:|---|
| Role alignment | 30 | Relationship between the actual work and the candidate's demonstrated specialization |
| Skills | 30 | Evidence for the stated essential and desired skills; identify missing essentials |
| Relevant experience | 25 | Direct or transferable work, research, methods, and scope supported by the selected resume |
| Education and seniority | 15 | Alignment of the degree and experience requirements with the candidate's current stage |

Unknown requirements earn no invented evidence; explain the uncertainty. Score
the opportunity independently of author verification. A high score cannot rescue
a failed or unknown required verification check. The default shortlist threshold
is 60; confirmed mismatches are excluded, and unknown verification goes to review.

## Outreach

For a shortlisted lead, write a 70–120 word LinkedIn direct-message draft. Address
the poster by their observed name, identify the role or team, mention one or two
specific supported qualifications, and ask briefly about fit or a conversation.
Do not invent shared connections, claim an attachment, promise availability that
conflicts with the candidate configuration, or imply that research has already
been published when its status is uncertain. Do not send the draft.
