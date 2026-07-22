# Example: Foreach and Condition

This blueprint demonstrates iterating over a file and conditionally running a phase.

```yaml
version: v1
blueprint:
  name: process_repos
  vars:
    repo_list: repos.txt

  phases:
    - id: read_list
      prompt: "Read {repo_list} and confirm it exists"
      allowed_tools: [Read]

    - id: clone_each
      prompt: "Clone repository: {item}"
      allowed_tools: [Bash]
      foreach: "{repo_list}"
      on_failure:
        action: skip

    - id: report_failures
      prompt: "List which repositories failed to clone"
      allowed_tools: []
      condition: "{{ not steps.clone_each.success }}"
```

## How It Works

1. `read_list` confirms the file exists.
2. `clone_each` runs once per non-empty line in `repos.txt`.
   - `{index}` is injected as the 0-based line number.
   - `{item}` is injected as the line content.
3. `report_failures` only runs if any clone failed, because of the `condition`.

## repos.txt

```
https://github.com/example/service-a.git
https://github.com/example/service-b.git
https://github.com/example/service-c.git
```
