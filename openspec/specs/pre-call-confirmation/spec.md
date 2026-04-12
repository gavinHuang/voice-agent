# pre-call-confirmation Specification

## Purpose
TBD - created by archiving change pre-call-field-confirmation. Update Purpose after archive.
## Requirements
### Requirement: Pre-call confirmation displays all context fields before dialing
The system SHALL display a formatted summary of the assembled `CallContext` to the operator before initiating any call. Required fields with values SHALL be shown clearly; optional fields that are `None` or empty SHALL be shown as "(not set)".

#### Scenario: Context summary is printed before dial
- **WHEN** the `voice-agent call` command is invoked
- **THEN** the terminal shows a structured summary of all `CallContext` fields before asking to proceed

#### Scenario: Empty optional fields shown as not set
- **WHEN** `caller_name` and `caller_context` are not provided
- **THEN** the summary displays "(not set)" for those fields rather than `None` or blank

### Requirement: Pre-call confirmation prompts for missing required fields
If any required field is absent when the command is invoked, the system SHALL interactively prompt the operator to supply it before proceeding. The call SHALL NOT be initiated until all required fields have values.

#### Scenario: Missing goal triggers prompt
- **WHEN** `voice-agent call <phone>` is run without `--goal` and no context file provides a goal
- **THEN** the operator is prompted to enter a goal, and the call does not proceed until one is provided

#### Scenario: All required fields present skips prompting
- **WHEN** `goal` is provided via flag or context file
- **THEN** no prompt for required fields is shown and confirmation proceeds directly to the "Proceed?" question

### Requirement: Operator must confirm before the call is dialed
After displaying the context summary, the system SHALL ask "Proceed with call? [y/N]" and only dial if the operator responds affirmatively. A non-affirmative response or empty input SHALL abort the call.

#### Scenario: Affirmative response dials
- **WHEN** the operator enters `y` or `Y`
- **THEN** the call is initiated

#### Scenario: Empty or negative response aborts
- **WHEN** the operator presses Enter or enters `n`
- **THEN** the call is aborted with a message "Call cancelled." and exit code 0

### Requirement: `--yes` flag bypasses interactive confirmation
When `--yes` (or `-y`) is passed, the system SHALL skip the interactive confirmation prompt and proceed immediately if all required fields are present. This flag is intended for scripted and CI use.

#### Scenario: --yes skips confirmation
- **WHEN** `voice-agent call <phone> --goal "..." --yes` is invoked
- **THEN** the context summary is printed but no "Proceed?" prompt is shown, and the call is initiated immediately

#### Scenario: --yes with missing required field still errors
- **WHEN** `--yes` is passed but `goal` is missing
- **THEN** the command exits with an error message and does not dial

