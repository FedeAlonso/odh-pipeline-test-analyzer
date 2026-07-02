# Create Component Skill

Generate a custom test failure analysis skill for a component based on its repository context, architecture, and existing Claude rules.

## Behavior

This skill generates a tailored `/analyze-failures` skill for a component by:

1. **Fetching Test Context**
   - Queries https://github.com/jctanner/odh-test-context for component-specific test documentation
   - Extracts test patterns, failure modes, and debugging steps

2. **Fetching Architecture Context**
   - Queries https://github.com/opendatahub-io/architecture-context for component architecture
   - Understands component dependencies, resources, and infrastructure

3. **Checking Repo Rules**
   - Looks for `.claude/rules/*.md` in the component's GitHub repo
   - Incorporates existing test rules and patterns (e.g., cypress-e2e.md)

4. **Analyzing Component Config**
   - Reads the component's `component.json`
   - Understands Jenkins jobs, test framework, namespaces, etc.

5. **Generating Custom Skill**
   - Creates `analyze-failures.md` skill tailored to the component
   - Includes component-specific failure categories
   - Embeds rerun commands, debugging steps, and context
   - Optionally generates additional skills (create-jira, rerun-flaky)

## Usage

```
/create-component-skill <github-repo-url> <framework>
/create-component-skill https://github.com/opendatahub-io/model-registry pytest
/create-component-skill https://github.com/opendatahub-io/odh-dashboard cypress --with-jira
```

## Parameters

- `github-repo-url` - Component's GitHub repository URL
- `framework` - Test framework (pytest, cypress, robot, golang, jest)
- `--with-jira` - Also generate Jira integration skill (optional)
- `--with-rerun` - Also generate flaky test rerun skill (optional)
- `--all-skills` - Generate full skill suite (analyze, jira, rerun, notify)

## Examples

**Example 1: Model Registry (minimal)**
```
User: /create-component-skill https://github.com/opendatahub-io/model-registry pytest

Assistant will:
1. Fetch odh-test-context for model-registry
2. Fetch architecture-context for model-registry
3. Check model-registry repo for .claude/rules/
4. Read component.json from .claude/components/model-registry/pytest/
5. Generate .claude/components/model-registry/pytest/skills/analyze-failures.md
6. Report: "✅ Created analyze-failures skill for model-registry/pytest"
```

**Example 2: Dashboard (with all skills)**
```
User: /create-component-skill https://github.com/opendatahub-io/odh-dashboard cypress --all-skills

Assistant will generate:
- analyze-failures.md (TFA skill)
- create-jira-ticket.md (Jira integration)
- rerun-flaky-tests.md (Automated reruns)
- notify-team.md (Slack/email notifications)
```

**Example 3: Update existing skill**
```
User: /create-component-skill https://github.com/opendatahub-io/model-registry pytest

# Regenerates skill with latest context
```

## Implementation Steps

When invoked, the skill performs these steps:

### Step 1: Parse Component Information

```python
# Extract component name from GitHub URL
# e.g., https://github.com/opendatahub-io/model-registry → "model-registry"
repo_url = args.github_repo_url
component_name = repo_url.split('/')[-1]
framework = args.framework

# Locate component.json
component_path = f".claude/components/{component_name}/{framework}"
config_file = f"{component_path}/component.json"

if not os.path.exists(config_file):
    print(f"❌ Component not found. Run onboarding first:")
    print(f"   python3 scripts/onboard_component.py")
    return
```

### Step 2: Fetch Test Context

```python
# Query odh-test-context repository
test_context_url = f"https://github.com/jctanner/odh-test-context"
test_context_file = f"docs/{component_name}-tests.md"

test_context = fetch_from_github(test_context_url, test_context_file)

# Parse test patterns, failure modes, debugging steps
test_patterns = parse_test_context(test_context)
```

**What we extract:**
- Common failure patterns
- Test categories/suites
- Known flaky tests
- Debugging commands
- Component-specific test rules

### Step 3: Fetch Architecture Context

```python
# Query architecture-context repository  
arch_context_url = f"https://github.com/opendatahub-io/architecture-context"
arch_file = f"components/{component_name}.md"

arch_context = fetch_from_github(arch_context_url, arch_file)

# Parse architecture details
architecture = parse_architecture(arch_context)
```

**What we extract:**
- Component dependencies
- Kubernetes resources (CRDs, deployments, services)
- Database requirements
- API endpoints
- Related components

### Step 4: Fetch Repo-Specific Rules

```python
# Check for .claude/rules in component's repo
rules_url = f"{repo_url}/tree/main/.claude/rules"
rules_files = list_github_files(rules_url)

component_rules = []
for rule_file in rules_files:
    if framework in rule_file or 'test' in rule_file:
        content = fetch_from_github(repo_url, f".claude/rules/{rule_file}")
        component_rules.append(content)
```

**Examples of what we find:**
- `cypress-e2e.md` for dashboard (Cypress best practices)
- `pytest-integration.md` (pytest patterns)
- `robot-framework.md` (Robot Framework conventions)

### Step 5: Read Component Configuration

```python
# Load component.json
with open(config_file) as f:
    config = json.load(f)

# Extract relevant info
jenkins_jobs = config['jenkins']['job_paths']
namespaces = config['cluster']['namespaces']
artifact_patterns = config['jenkins']['artifact_patterns']
failure_categories = config['analysis']['failure_categories']
```

### Step 6: Generate Custom Skill

```python
skill_template = f"""# Analyze {component_name.title()} Test Failures

Analyze test failures for the {component_name} component from Jenkins builds.

## Behavior

1. **Fetch Build Results**
   - Jenkins jobs: {', '.join(jenkins_jobs)}
   - Artifacts: {', '.join(artifact_patterns)}

2. **Categorize Failures**
{format_failure_categories(failure_categories, test_patterns)}

3. **Check Cluster Health**
   - Namespaces: {', '.join(namespaces)}
   - Resources: {format_k8s_resources(architecture)}

4. **Provide Context**
{format_debugging_steps(test_patterns, component_rules)}

## Usage

```
/analyze-failures
/analyze-failures --build <number>
/analyze-failures --verbose
```

## Component Context

### Repository
- **Repo**: {repo_url}
- **Framework**: {framework}
- **Test location**: {config['test_framework']['test_directory']}

### Architecture
{format_architecture(architecture)}

### Common Failure Patterns
{format_test_patterns(test_patterns)}

### Debugging Steps
{format_debugging_from_rules(component_rules)}

### Rerun Commands
{format_rerun_commands(config, test_patterns)}

## Implementation

```python
# Use platform's analyze_component.py
result = await analyze_component_build(
    '{component_name}',
    '{framework}',
    build_number,
    verbose=args.get('verbose', False)
)

# Display categorized failures
for category, failures in result['by_category'].items():
    print(f"{{category}}: {{len(failures)}} failures")
    for failure in failures:
        print(f"  - {{failure['test_name']}}")
        print(f"    Error: {{failure['error_message'][:100]}}")
        print(f"    Rerun: {{failure['rerun_command']}}")
```

## Team Information

- **Maintainers**: {', '.join(config.get('maintainers', []))}
- **Jira Project**: [Update if available]
- **Slack**: [Update with team channel]

## Next Steps

After a failure is identified:
1. Check cluster health: `oc get pods -n {{namespace}}`
2. Review logs: `oc logs {{pod_name}} -n {{namespace}}`
3. Rerun test: `{{rerun_command}}`
4. Create Jira if needed: `/create-jira-ticket` (if enabled)
"""

# Write skill file
skill_path = f"{component_path}/skills/analyze-failures.md"
os.makedirs(f"{component_path}/skills", exist_ok=True)
with open(skill_path, 'w') as f:
    f.write(skill_template)

print(f"✅ Created analyze-failures skill at: {skill_path}")
```

### Step 7: Generate Additional Skills (if requested)

**Jira Integration Skill** (`--with-jira`):
```markdown
# Create Jira Ticket

Create a Jira ticket for a test failure.

## Usage
```
/create-jira-ticket <test-name> <build-number>
```

## Implementation
- Uses Jira API to create ticket
- Populates with failure details, stack trace, Jenkins link
- Tags with component label
- Assigns to component team (from config)
```

**Rerun Flaky Tests Skill** (`--with-rerun`):
```markdown
# Rerun Flaky Tests

Automatically rerun tests that failed due to known flaky issues.

## Behavior
- Identifies flaky tests from failure patterns
- Triggers Jenkins job to rerun specific tests
- Reports results

## Usage
```
/rerun-flaky-tests --build <number>
```
```

## Context Sources

### 1. odh-test-context Repository

**Location**: https://github.com/jctanner/odh-test-context

**Structure**:
```
docs/
├── dashboard-tests.md       # Dashboard test documentation
├── model-registry-tests.md  # Model registry test patterns
├── workbenches-tests.md     # Workbenches test info
└── ...
```

**What we extract**:
- Test suite descriptions
- Known failure modes
- Flaky test patterns
- Debug procedures
- Historical context

### 2. architecture-context Repository

**Location**: https://github.com/opendatahub-io/architecture-context

**Structure**:
```
components/
├── dashboard.md         # Dashboard architecture
├── model-registry.md    # Model registry architecture
├── kserve.md           # KServe architecture
└── ...
```

**What we extract**:
- Component dependencies
- Kubernetes resources (Deployments, Services, CRDs)
- Database dependencies
- API contracts
- Related components

### 3. Component Repository Rules

**Example**: https://github.com/opendatahub-io/odh-dashboard/.claude/rules/cypress-e2e.md

**What we extract**:
- Framework-specific best practices
- Test patterns and anti-patterns
- Common pitfalls
- Debugging techniques
- Team conventions

### 4. Component Configuration

**Source**: `.claude/components/{component}/{framework}/component.json`

**What we use**:
- Jenkins job paths
- Artifact patterns
- Namespaces
- Failure categories
- Maintainer info

## Output

The skill generates this file structure:

```
.claude/components/{component-name}/{framework}/
├── component.json                    # Already exists (from onboarding)
└── skills/
    ├── analyze-failures.md           # Generated TFA skill
    ├── create-jira-ticket.md         # Optional (--with-jira)
    ├── rerun-flaky-tests.md          # Optional (--with-rerun)
    └── notify-team.md                # Optional (--all-skills)
```

## Customization

Teams can edit the generated skills to:
- Add component-specific failure patterns
- Update Jira project/labels
- Customize notification channels
- Add additional debugging commands
- Include links to team dashboards

## Error Handling

**If test-context not found**:
```
⚠️  No test context found for {component}
    Generating skill with basic template
    To improve: Add docs/{component}-tests.md to odh-test-context repo
```

**If architecture-context not found**:
```
⚠️  No architecture context found for {component}
    Skill will use component.json only
    To improve: Add components/{component}.md to architecture-context repo
```

**If no repo rules found**:
```
ℹ️  No .claude/rules found in repo
    Using framework defaults only
    To improve: Add .claude/rules/{framework}.md to component repo
```

## Example: Dashboard Component

**Command**:
```
/create-component-skill https://github.com/opendatahub-io/odh-dashboard cypress --all-skills
```

**Process**:
1. Fetch https://github.com/jctanner/odh-test-context/docs/dashboard-tests.md
2. Fetch https://github.com/opendatahub-io/architecture-context/components/dashboard.md
3. Fetch https://github.com/opendatahub-io/odh-dashboard/.claude/rules/cypress-e2e.md
4. Read .claude/components/dashboard/cypress/component.json
5. Generate skills with:
   - Cypress-specific failure patterns (from cypress-e2e.md)
   - Dashboard architecture (from architecture-context)
   - Known flaky tests (from test-context)
   - Team conventions (from repo rules)

**Result**: Comprehensive skill suite ready for dashboard team to use

## Integration with Onboarding

Update `ONBOARDING_GUIDE.md` to include:

```markdown
## Step 4: Generate Team Skills (New!)

After creating your component.json, generate custom skills:

```bash
/create-component-skill https://github.com/{org}/{your-repo} {framework}
```

This creates a `/analyze-failures` skill tailored to your component using:
- Test context from odh-test-context
- Architecture from architecture-context  
- Your repo's .claude/rules
- Your component.json configuration

Then use it:
```bash
/analyze-failures --build 123
```
```

## Future Enhancements

- [ ] Auto-detect Jira project from component context
- [ ] Suggest failure categories based on test-context patterns
- [ ] Generate skill for historical trend analysis
- [ ] Integration with notification systems (Slack, PagerDuty)
- [ ] Auto-update skills when context repos change
