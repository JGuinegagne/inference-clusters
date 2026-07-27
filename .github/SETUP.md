# CI infrastructure setup

The AWS-touching workflows (roborev review + e2e) need a one-time deploy of the
`jupyter-infra-tf-aws-iam-ci` template into a **dedicated CI account**. They stay inert until
the repo variables are set, so `.github/` can live on `main` before this exists.

1. **Deploy the CI template** into a fresh AWS account:
   ```bash
   pip install "jupyter-deploy[aws]" jupyter-infra-tf-aws-iam-ci
   mkdir sandbox-ci && cd sandbox-ci
   jd init . -E terraform -P aws -I iam -T ci
   # set overrides (region, *-prefix, create_review_resources=true, publish_repo/review_repos);
   # OAuth/bot vars take throwaway placeholders — this repo has no GitHub apps.
   jd config && jd up
   ```
   Region is baked in (not mutable in place). `jd` manages its own project-store bucket.

2. **Create GitHub environments** `e2e` and `review`; give `review` protection rules
   (restrict to `main`) — OIDC only proves the env was declared, not that the workflow was trusted.

3. **Set repo variables** from the tf outputs (`jd show -o <name> --text -p sandbox-ci`):
   `AWS_E2E_ROLE_ARN`, `AWS_REGION`, `REVIEW_PUBLISH_ROLE_ARN`, `REVIEW_ECR_REPOSITORY`,
   `REVIEW_RUN_ROLE_ARN`, `REVIEW_IMAGE` (`…/review:latest`), `REVIEW_BEDROCK_MODEL`.

4. **Publish the review image before the consume vars go live**: set the two `REVIEW_PUBLISH_*`
   vars, run `review-build-image.yml`, then set the rest. Otherwise every PR's review job fails
   pulling a missing image. Smoke-test with `roborev-review.yml` (`smoke: true`).
