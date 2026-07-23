# sns-topic common tests

Cross-example tests covering both `examples/basic` and `examples/with-subscriptions`.

Required because the sns-topic module ships more than one example (tests_policy.rego T11).

Asserts (across both examples):
- Terraform version satisfies the pinned constraint (1.15.5)
- Both required outputs (`topic_arn`, `topic_name`) are present and well-formed
- `topic_arn` matches `^arn:aws:sns:[a-z0-9-]+:[0-9]{12}:`
- `topic_name` equals the provisioned topic name
- Apply is idempotent for both examples
