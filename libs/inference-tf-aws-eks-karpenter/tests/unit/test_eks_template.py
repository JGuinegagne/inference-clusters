"""HCL structure + resource-ordering assertions for the engine.

Mirrors the eks-oidc exhaustivity test: pins the load-bearing depends_on edges so
the create/destroy ordering can't silently regress.
"""

import re

import yaml

from inference_tf_aws_eks_karpenter.template import TEMPLATE_PATH

ENGINE = TEMPLATE_PATH / "engine"


def test_karpenter_chart_version_matches_template_version() -> None:
    """charts/karpenter Chart.yaml version must track the template version (SemVer spelling).

    Template version 0.1.0rc1 (PEP 440) == SemVer 0.1.0-rc1; Helm requires SemVer.
    """
    manifest = yaml.safe_load((TEMPLATE_PATH / "manifest.yaml").read_text())
    template_version = manifest["template"]["version"]
    semver = template_version.replace("rc", "-rc")
    chart = yaml.safe_load((TEMPLATE_PATH / "charts" / "karpenter" / "Chart.yaml").read_text())
    assert chart["version"] == semver, (
        f"charts/karpenter version ({chart['version']}) must equal SemVer of template version ({semver})"
    )


def _extract_resource_block(content: str, resource_type: str, resource_name: str) -> str:
    """Return the body of a `resource "<type>" "<name>" { ... }` block."""
    start = re.search(
        rf'resource\s+"{re.escape(resource_type)}"\s+"{re.escape(resource_name)}"\s*\{{',
        content,
    )
    assert start is not None, f"resource {resource_type}.{resource_name} not found"

    depth = 1
    idx = start.end()
    while idx < len(content) and depth > 0:
        if content[idx] == "{":
            depth += 1
        elif content[idx] == "}":
            depth -= 1
        idx += 1
    return content[start.end() : idx - 1]


def _extract_resource_block_module(content: str, module_name: str) -> str:
    """Return the body of a `module "<name>" { ... }` block (brace-matched)."""
    start = re.search(rf'module\s+"{re.escape(module_name)}"\s*\{{', content)
    assert start is not None, f"module {module_name} not found"
    depth, idx = 1, start.end()
    while idx < len(content) and depth > 0:
        if content[idx] == "{":
            depth += 1
        elif content[idx] == "}":
            depth -= 1
        idx += 1
    return content[start.end() : idx - 1]


def _extract_depends_on_names(block: str, resource_type: str) -> set[str]:
    """Return the set of `<resource_type>` names referenced in a depends_on list."""
    match = re.search(r"depends_on\s*=\s*\[(.*?)\]", block, re.DOTALL)
    assert match is not None, "no depends_on block found"
    refs = re.findall(rf"{re.escape(resource_type)}\.(\w+)", match.group(1))
    return set(refs)


def test_all_eks_addons_gated_by_cluster_addons() -> None:
    """Every aws_eks_addon MUST appear in null_resource.cluster_addons.depends_on.

    This barrier keeps all cluster addons alive until every Helm chart
    has uninstalled. A new addon not wired into the aggregator silently regresses
    the destroy ordering.
    """
    content = (ENGINE / "eks_addons.tf").read_text()

    declared_addons = set(re.findall(r'resource\s+"aws_eks_addon"\s+"(\w+)"', content))
    assert declared_addons, "no aws_eks_addon resources found in eks_addons.tf"

    cluster_addons_block = _extract_resource_block(content, "null_resource", "cluster_addons")
    gated_addons = _extract_depends_on_names(cluster_addons_block, "aws_eks_addon")

    missing = declared_addons - gated_addons
    assert not missing, (
        f"aws_eks_addon(s) {sorted(missing)} are not listed in "
        "null_resource.cluster_addons.depends_on — Helm chart destroy ordering will "
        "silently regress. Add them to the aggregator in eks_addons.tf."
    )


def test_cluster_addons_gates_admin_access_associations() -> None:
    """null_resource.cluster_addons MUST depend on the admin access-policy associations
    (and the node access entry).

    These associations authorize the Helm/Kubernetes providers to reach the cluster.
    Every Helm release routes through cluster_addons, so on destroy the associations
    must outlive the charts — otherwise the providers lose authorization mid-destroy and
    remaining uninstalls fail "forbidden" (the eks-oidc lesson). Dropping this edge
    silently regresses the teardown.
    """
    content = (ENGINE / "eks_addons.tf").read_text()
    cluster_addons_block = _extract_resource_block(content, "null_resource", "cluster_addons")

    gated_assoc = _extract_depends_on_names(cluster_addons_block, "aws_eks_access_policy_association")
    assert {"admin_role", "admin_user"} <= gated_assoc, (
        "null_resource.cluster_addons.depends_on must include "
        "aws_eks_access_policy_association.admin_role and .admin_user — else Helm/K8s "
        'providers can lose authz mid-destroy and uninstalls fail "forbidden".'
    )

    gated_entry = _extract_depends_on_names(cluster_addons_block, "aws_eks_access_entry")
    assert "node" in gated_entry, (
        "null_resource.cluster_addons.depends_on must include aws_eks_access_entry.node "
        "so nodes stay joined until every chart has uninstalled."
    )


def test_core_node_addons_are_daemonsets_only() -> None:
    """core_node_addons must gate ONLY vpc-cni and kube-proxy.

    The system node group depends_on core_node_addons; a Deployment addon (coredns,
    ebs-csi) needs a schedulable node, so gating the node group on it would be a
    create-time cycle.
    """
    content = (ENGINE / "eks_addons.tf").read_text()
    block = _extract_resource_block(content, "null_resource", "core_node_addons")
    gated = _extract_depends_on_names(block, "aws_eks_addon")
    assert gated == {"vpc_cni", "kube_proxy"}, (
        f"core_node_addons should gate exactly vpc_cni + kube_proxy, got {sorted(gated)}"
    )


def test_node_group_depends_on_core_node_addons() -> None:
    """The system node group must come up after CNI/kube-proxy and tear down before them."""
    content = (ENGINE / "main.tf").read_text()
    match = re.search(r"module\s+\"node_group\".*?\n\}", content, re.DOTALL)
    assert match is not None, "module.node_group block not found"
    assert "null_resource.core_node_addons" in match.group(0), (
        "module.node_group must depend_on null_resource.core_node_addons"
    )


def test_node_group_depends_on_node_access_entry() -> None:
    """The system node group MUST depend on aws_eks_access_entry.node.

    With EKS API auth mode the EC2_LINUX access entry authorizes the node role to join.
    Without the edge the node group and the entry are siblings, so nodes may be created
    before the entry exists → they never register and creation fails with
    NodeCreationFailure ("new nodes are not joining"). On destroy the reverse edge keeps
    the entry alive until the nodes are gone.
    """
    content = (ENGINE / "main.tf").read_text()
    match = re.search(r"module\s+\"node_group\".*?\n\}", content, re.DOTALL)
    assert match is not None, "module.node_group block not found"
    assert "aws_eks_access_entry.node" in match.group(0), (
        "module.node_group must depend_on aws_eks_access_entry.node so nodes can join "
        "(API auth mode) — else creation fails NodeCreationFailure."
    )


def test_node_role_registered_as_access_entry() -> None:
    """The node role MUST have an EC2_LINUX access entry so nodes can join (API auth mode)."""
    content = (ENGINE / "main.tf").read_text()
    block = _extract_resource_block(content, "aws_eks_access_entry", "node")
    assert 'type          = "EC2_LINUX"' in block or 'type = "EC2_LINUX"' in block.replace("  ", " "), (
        "node access entry must be type EC2_LINUX"
    )
    assert "module.node_role.role_arn" in block


def test_system_node_group_is_tainted_and_labeled() -> None:
    """The system NG must carry the inference/role=system label AND taint."""
    content = (ENGINE / "main.tf").read_text()
    match = re.search(r"module\s+\"node_group\".*?\n\}", content, re.DOTALL)
    assert match is not None
    block = match.group(0)
    assert '"inference/role" = "system"' in block, "system NG must be labeled inference/role=system"
    assert "NO_SCHEDULE" in block, "system NG must be tainted NoSchedule"


# --- Pull-through image supply ---


def test_trusted_upstreams_are_no_credentials_only() -> None:
    """trusted_upstreams MUST contain ONLY the three no-credentials upstreams.

    ECR pull-through offers anonymous rules for exactly ECR Public, registry.k8s.io,
    and Quay. Docker Hub/GHCR/etc. require a Secrets Manager secret we refuse to own
    (verified against AWS docs 2026-07-02). A credentialed host slipping in here
    would need a credential_arn or fail at apply.
    """
    content = (ENGINE / "images.tf").read_text()
    block = re.search(r"trusted_upstreams\s*=\s*\{(.*?)\n  \}", content, re.DOTALL)
    assert block is not None, "trusted_upstreams local not found in images.tf"
    hosts = set(re.findall(r'url\s*=\s*"([^"]+)"', block.group(1)))
    assert hosts == {"public.ecr.aws", "quay.io", "registry.k8s.io"}, (
        f"trusted_upstreams must be exactly the 3 no-credentials upstreams, got {sorted(hosts)}"
    )


def test_node_role_has_pullthrough_import_permissions() -> None:
    """The node role MUST be granted ecr import-on-miss (the pull-through allowlist)."""
    content = (ENGINE / "images.tf").read_text()
    assert "ecr:BatchImportUpstreamImage" in content, "node role missing BatchImportUpstreamImage"
    assert "ecr:CreateRepository" in content, "node role missing CreateRepository"
    assert "aws_iam_role_policy" in content and "node_pullthrough" in content, (
        "node-role inline pull-through policy not wired"
    )


def test_pullthrough_ready_barrier_gates_shared_infra_and_iam() -> None:
    """null_resource.pullthrough_ready MUST depend on the shared pull-through infra
    (pullthrough.tf) + the node import IAM.

    The node identity policy is the sufficient, documented import-on-miss grant; no
    aws_ecr_registry_policy (a redundant one failed PutRegistryPolicy — see images.tf).
    """
    content = (ENGINE / "images.tf").read_text()
    block = _extract_resource_block(content, "null_resource", "pullthrough_ready")
    for dep in (
        "null_resource.pullthrough_infra",
        "aws_iam_role_policy.node_pullthrough",
    ):
        assert dep in block, f"pullthrough_ready.depends_on missing {dep}"
    assert 'resource "aws_ecr_registry_policy"' not in content, (
        "registry policy is redundant with the node identity grant and failed PutRegistryPolicy"
    )


def test_pullthrough_infra_is_shared_singleton_not_tf_resource() -> None:
    """The pull-through cache RULE and creation TEMPLATE are account-regional singletons
    (one per prefix per account+region), so they MUST NOT be Terraform-managed resources.

    As TF resources with a fixed prefix they collide across two deployments in the same
    account+region: the second apply fails AlreadyExists and the first `jd down` deletes
    them out from under the survivor. They are provisioned imperatively in pullthrough.tf
    instead (create-if-absent / adopt / fail-on-divergence, never deleted on destroy).
    """
    for f in ("images.tf", "pullthrough.tf"):
        content = (ENGINE / f).read_text()
        assert 'resource "aws_ecr_pull_through_cache_rule"' not in content, (
            f"{f}: pull-through rule must be imperative (shared singleton), not a TF resource"
        )
        assert 'resource "aws_ecr_repository_creation_template"' not in content, (
            f"{f}: creation template must be imperative (shared singleton), not a TF resource"
        )
    # The per-deployment creation role is gone: dropping resource_tags removes the only
    # thing that forced a custom_role_arn (tags/KMS), so no shared role to manage.
    assert 'resource "aws_iam_role" "ecr_template_creation"' not in (ENGINE / "images.tf").read_text()


def test_pullthrough_infra_ensure_script_semantics() -> None:
    """pullthrough.tf MUST ensure the shared rule + template create-if-absent / adopt /
    fail-on-divergence, with NO destroy provisioner (shared infra outlives any deployment).
    """
    content = (ENGINE / "pullthrough.tf").read_text()
    block = _extract_resource_block(content, "null_resource", "pullthrough_infra")

    # Imperative CLI ensure (not a declarative resource).
    assert "create-pull-through-cache-rule" in block, "must create the cache rule when absent"
    assert "create-repository-creation-template" in block, "must create the template when absent"
    assert "describe-pull-through-cache-rules" in block, "must probe existing rule for adopt/diverge"
    assert "describe-repository-creation-templates" in block, "must probe existing template"

    # Fail-closed on divergence of the attributes that matter.
    assert block.count("exit 1") >= 2, "must FAIL on a divergent pre-existing rule/template"
    assert "PULL_THROUGH_CACHE" in block

    # Bash interpreter per repo convention; create-time only (no destroy = leave on down).
    assert 'interpreter = ["/bin/bash", "-c"]' in block, "local-exec must use bash"
    assert "when        = destroy" not in block and "when = destroy" not in block, (
        "shared pull-through infra must NOT be torn down on destroy (it outlives the deployment)"
    )


def test_bootstrap_ami_type_resolved_at_root_not_in_module() -> None:
    """ami_type MUST be resolved at the root and passed in concrete.

    A data source inside the node_group module inherits the module's depends_on, so
    it defers to apply-time when an upstream dep changes → ami_type "known after
    apply" → node group REPLACEMENT on every re-apply. Resolving at the root keeps it
    plan-time-stable. (Diagnosed live 2026-07-03.)
    """
    main = (ENGINE / "main.tf").read_text()
    assert re.search(r'data\s+"aws_ec2_instance_type"\s+"bootstrap"', main), (
        "root must own the instance-type data source (not the node_group module)"
    )
    call = re.search(r"module\s+\"node_group\".*?\n\}", main, re.DOTALL)
    assert call is not None
    assert re.search(r"ami_type\s*=\s*local\.bootstrap_ami_type", call.group(0)), (
        "node_group must be called with the root-resolved local.bootstrap_ami_type"
    )
    assert '"default"' not in call.group(0), "ami_type must be concrete, never 'default'"
    module_main = (ENGINE / "modules" / "node_group" / "main.tf").read_text()
    assert 'data "aws_ec2_instance_type"' not in module_main, (
        "node_group module must NOT contain a data source (depends_on cascade forces replacement)"
    )


def test_node_group_depends_on_pullthrough_ready() -> None:
    """The bootstrap NG must not boot before the pull-through path exists."""
    content = (ENGINE / "main.tf").read_text()
    match = re.search(r"module\s+\"node_group\".*?\n\}", content, re.DOTALL)
    assert match is not None
    assert "null_resource.pullthrough_ready" in match.group(0), (
        "module.node_group must depend_on null_resource.pullthrough_ready"
    )


def test_node_group_launch_template_carries_mirror_userdata() -> None:
    """The node_group launch template must inject the containerd mirror userData (backup)."""
    content = (ENGINE / "modules" / "node_group" / "main.tf").read_text()
    assert "aws_launch_template" in content, "node_group must define a launch template"
    assert "userdata.sh.tftpl" in content, "launch template must render the mirror userData template"
    tftpl = (ENGINE / "modules" / "node_group" / "userdata.sh.tftpl").read_text()
    assert "config_path" in tftpl and "certs.d" in tftpl, "userData must set containerd certs.d config_path"
    assert "node.eks.aws" in tftpl, "userData must be a nodeadm NodeConfig MIME part"


# --- Karpenter ---


def test_karpenter_controller_policy_is_cluster_scoped() -> None:
    """The Karpenter controller policy MUST scope EC2 create/delete by the cluster tag.

    An unconditioned RunInstances/TerminateInstances grant would let the controller
    act on any instance in the account. The published v1 policy scopes by
    kubernetes.io/cluster/<name>=owned and karpenter.sh/nodepool.
    """
    content = (ENGINE / "iam.tf").read_text()
    assert 'data "aws_iam_policy_document" "karpenter_controller"' in content
    assert "kubernetes.io/cluster/" in content, "controller policy not scoped by cluster tag"
    assert "ec2:TerminateInstances" in content and "ec2:RunInstances" in content
    # Attached directly (module.iam_policy can't express conditions).
    assert "aws_iam_role_policy" in content and "karpenter_controller" in content


def test_nodeclass_uses_precreated_instance_profile_not_role() -> None:
    """EC2NodeClass MUST use a pre-created instanceProfile, never `role`.

    On the endpoints-only VPC Karpenter can't reach IAM (no VPC endpoint), so
    a `role` (which makes Karpenter manage the profile via IAM) hangs the reconcile
    and every downstream controller misreports "no subnets found". The profile is
    pre-created in Terraform and injected. (Diagnosed live 2026-07-03.)
    """
    iam = (ENGINE / "iam.tf").read_text()
    assert 'resource "aws_iam_instance_profile" "node"' in iam, "node instance profile must be pre-created in Terraform"
    nodeclass = (TEMPLATE_PATH / "charts" / "karpenter" / "templates" / "ec2nodeclass.yaml").read_text()
    assert "instanceProfile:" in nodeclass, "EC2NodeClass must set instanceProfile"
    assert not re.search(r"^\s*role:", nodeclass, re.MULTILINE), (
        "EC2NodeClass must NOT set `role` (forces Karpenter to call IAM — unreachable on endpoints-only VPC)"
    )
    # The controller policy must no longer manage instance profiles (no IAM calls).
    assert "iam:CreateInstanceProfile" not in iam, (
        "controller must not manage instance profiles — the profile is pre-created"
    )
    tf = (ENGINE / "platform_karpenter.tf").read_text()
    assert "aws_iam_instance_profile.node.name" in tf, (
        "nodeInstanceProfile value must be injected from the pre-created profile"
    )


def test_interruption_queue_has_no_spot_rule() -> None:
    """The interruption queue exists; EventBridge rules must NOT include spot (on-demand only)."""
    content = (ENGINE / "platform_karpenter.tf").read_text()
    assert "aws_sqs_queue" in content and "karpenter_interruption" in content
    assert "Spot Interruption" not in content, "no spot interruption rule — pools are on-demand only"


def test_karpenter_drain_triggers_are_attribute_refs() -> None:
    """The drain poller's triggers MUST reference controller + cluster + access entry as attributes.

    These attribute refs are the load-bearing destroy edges: they keep Karpenter,
    the API server, and the poller's authz alive for the whole drain. A captured
    string would silently drop the edge and re-introduce the orphan-node hang.
    """
    content = (ENGINE / "platform_karpenter.tf").read_text()
    block = _extract_resource_block(content, "null_resource", "karpenter_drain")
    assert "helm_release.karpenter.id" in block, "drain must reference the controller release attribute"
    assert "module.eks_cluster.cluster_endpoint" in block, "drain must reference the cluster endpoint attribute"
    assert "aws_eks_access_entry" in block, "drain must reference an access entry attribute"
    assert "when        = destroy" in block or "when = destroy" in block.replace("  ", " "), (
        "drain provisioner must run on destroy"
    )


def test_karpenter_chart_pull_is_unauthenticated() -> None:
    """The Karpenter helm_release MUST NOT set chart-pull auth.

    public.ecr.aws serves the chart anonymously (verified live). A minted
    ecr-public token in repository_password is minted fresh every plan → perpetual
    diff → the release UPDATEs every apply → recreated null_resource.karpenter_drain
    (trigger references the release id) → destroy drain wipes ALL NodePools; and the
    token also goes stale in state and 403s the plan-time refresh. No token avoids
    all of it. (Diagnosed live 2026-07-04.)
    """
    content = (ENGINE / "platform_karpenter.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "karpenter")
    assert "repository_password" not in block, (
        "karpenter chart pull must be anonymous — no repository_password (perpetual-diff + stale-token trap)"
    )
    assert "repository_username" not in block
    # The now-unused public-ECR token data source + provider alias must be gone too.
    main = (ENGINE / "main.tf").read_text()
    assert "aws_ecrpublic_authorization_token" not in main, "unused ecr-public token data source must be removed"
    assert "ecr_public" not in main, "unused us-east-1 ecr_public provider alias must be removed"


def test_nodepool_release_sandwiched_by_drain() -> None:
    """The NodePool release MUST depend on the drain poller so NodePools delete BEFORE the drain."""
    content = (ENGINE / "platform_karpenter.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "karpenter_nodepools")
    assert "null_resource.karpenter_drain" in block, "karpenter_nodepools must depend_on null_resource.karpenter_drain"
    assert "helm_release.karpenter" in block, "NodePools must depend_on the controller release"


# --- Storage ---


def _extract_data_block(content: str, data_type: str, data_name: str) -> str:
    """Return the body of a `data "<type>" "<name>" { ... }` block (brace-matched)."""
    start = re.search(rf'data\s+"{re.escape(data_type)}"\s+"{re.escape(data_name)}"\s*\{{', content)
    assert start is not None, f"data {data_type}.{data_name} not found"
    depth, idx = 1, start.end()
    while idx < len(content) and depth > 0:
        if content[idx] == "{":
            depth += 1
        elif content[idx] == "}":
            depth -= 1
        idx += 1
    return content[start.end() : idx - 1]


def test_node_s3_grant_scoped_to_bucket_not_star() -> None:
    """The node-role S3 grant (S3-direct path) MUST be scoped to the bucket ARN, never `*`."""
    content = (ENGINE / "platform_storage.tf").read_text()
    block = _extract_data_block(content, "aws_iam_policy_document", "node_s3")
    assert "module.model_store.bucket_arn" in block, "node S3 grant must reference the bucket ARN"
    assert '"*"' not in block, "node S3 grant must never use Resource '*'"
    # Reads anywhere in the bucket; writes only under output/.
    assert "s3:GetObject" in block and "s3:PutObject" in block
    assert "output" in block, "write grant must be scoped to the output/ prefix"


def test_s3_csi_uses_dedicated_pod_identity_role() -> None:
    """Mountpoint-for-S3 auths via a DEDICATED Pod Identity role, not the node role (least-privilege)."""
    storage = (ENGINE / "platform_storage.tf").read_text()
    assert 'module "s3_csi_role"' in storage, "a dedicated s3_csi_role must exist"
    csi_doc = _extract_data_block(storage, "aws_iam_policy_document", "s3_csi")
    assert '"*"' not in csi_doc, "s3_csi grant must never use Resource '*'"
    assert "s3:GetObject" in csi_doc, "mountpoint role must read objects"
    # Addon wires that role via Pod Identity.
    addons = (ENGINE / "eks_addons.tf").read_text()
    s3_addon = _extract_resource_block(addons, "aws_eks_addon", "s3_csi_driver")
    assert "aws-mountpoint-s3-csi-driver" in s3_addon, "must install the Mountpoint-for-S3 CSI driver"
    assert "module.s3_csi_role.role_arn" in s3_addon, "s3 CSI addon must use the dedicated role via Pod Identity"


def test_s3_csi_addon_in_cluster_addons_barrier() -> None:
    """The S3 CSI driver MUST be in the cluster_addons aggregator so charts teardown before it."""
    content = (ENGINE / "eks_addons.tf").read_text()
    block = _extract_resource_block(content, "null_resource", "cluster_addons")
    assert "aws_eks_addon.s3_csi_driver" in block, "s3_csi_driver must be in the cluster_addons barrier"


def test_storage_chart_ships_two_storageclasses() -> None:
    """charts/storage MUST ship the EBS gp3 default class AND the S3-mount static PV/PVC."""
    ebs = (TEMPLATE_PATH / "charts" / "storage" / "templates" / "ebs-storageclass.yaml").read_text()
    assert "ebs.csi.aws.com" in ebs, "EBS StorageClass must use the ebs CSI provisioner"
    assert "is-default-class" in ebs, "EBS gp3 must be markable as the default class"
    s3 = (TEMPLATE_PATH / "charts" / "storage" / "templates" / "s3-mount.yaml").read_text()
    assert "s3.csi.aws.com" in s3, "S3 mount must use the Mountpoint CSI driver"
    assert "read-only" in s3, "the S3 model mount must be read-only (writes go via S3-direct)"
    assert "PersistentVolume" in s3 and "PersistentVolumeClaim" in s3, (
        "Mountpoint supports static provisioning only — chart must ship a PV + PVC"
    )


def test_storage_release_ordered_after_csi_drivers() -> None:
    """The storage helm_release MUST depend on the CSI drivers + cluster_addons (create/teardown order)."""
    content = (ENGINE / "platform_storage.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "storage")
    assert "null_resource.cluster_addons" in block, "storage chart must depend_on cluster_addons"
    assert "aws_eks_addon.s3_csi_driver" in block, "storage chart must depend_on the S3 CSI driver"


def test_model_store_bucket_is_exported() -> None:
    """The shared bucket name + ARN MUST be outputs (consumers consume them)."""
    content = (ENGINE / "outputs.tf").read_text()
    assert "model_store_bucket" in content, "bucket name must be an output"
    assert "model_store_bucket_arn" in content, "bucket ARN must be an output"


def test_ec2nodeclass_imds_hop_limit_allows_pod_creds() -> None:
    """EC2NodeClasses MUST set IMDS hop limit 2 so pods reach node-role creds (S3-direct).

    Karpenter's default hop limit is 1; a containerized process is one hop beyond the
    host, so at 1 a pod can't reach IMDS → node-role creds invisible → S3-direct fails
    "Unable to locate credentials". (Verified live.)
    """
    content = (TEMPLATE_PATH / "charts" / "karpenter" / "templates" / "ec2nodeclass.yaml").read_text()
    assert "httpPutResponseHopLimit: 2" in content, (
        "EC2NodeClass must set IMDS hop limit 2 or pods can't use node-role creds"
    )
    # All three classes (cpu + gpu + gpu-p) must carry it — count the metadataOptions blocks.
    assert content.count("httpPutResponseHopLimit: 2") == 3, (
        "cpu, gpu, and gpu-p EC2NodeClasses must all set the hop limit"
    )


# --- Observability ---


def test_prometheus_images_pinned_to_pullthrough() -> None:
    """Every kube-prometheus-stack image MUST resolve to a pull-through registry, never docker.io/ghcr.io."""
    content = (ENGINE / "platform_prometheus.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "kube_prometheus_stack")
    # All image registries route through local.quay_registry / local.k8s_registry.
    assert "local.quay_registry" in block, "prometheus images must pin to the quay pull-through prefix"
    assert "local.k8s_registry" in block, "kube-state-metrics must pin to the registry-k8s pull-through prefix"
    # Strip comment lines before asserting no untrusted registry is actually configured.
    code = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
    assert "docker.io" not in code and "ghcr.io" not in code, (
        "no image may reference docker.io/ghcr.io (not no-creds pull-through upstreams)"
    )


def test_prometheus_admission_webhook_disabled() -> None:
    """The admission webhook MUST be disabled — its cert-gen job pulls from ghcr.io."""
    content = (ENGINE / "platform_prometheus.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "kube_prometheus_stack")
    assert "admissionWebhooks" in block and "enabled = false" in block, (
        "admissionWebhooks must be disabled (ghcr.io cert-gen dependency)"
    )


def test_prometheus_has_memory_limit_on_system_ng() -> None:
    """Prometheus MUST carry a memory limit + system-NG placement (isolation on the fixed NG)."""
    content = (ENGINE / "platform_prometheus.tf").read_text()
    block = _extract_resource_block(content, "helm_release", "kube_prometheus_stack")
    assert "prometheus_memory_limit" in block, "Prometheus must set a memory limit (OOM isolation)"
    assert "system_node_selector" in block and "system_toleration" in block, (
        "control-loop components must be pinned to the tainted system NG"
    )


def test_dcgm_exporter_vendored_gpu_only_and_scraped() -> None:
    """DCGM is nvcr.io-only → vendored; runs GPU-nodes-ONLY; ServiceMonitor carries the release label."""
    images = (ENGINE / "images.tf").read_text()
    assert "dcgm_exporter" in images, "dcgm-exporter must be in the vendored_images map (images.tf)"
    assert "nvcr.io/nvidia/k8s/dcgm-exporter" in images, "DCGM source must be the nvcr.io image"
    prom = (ENGINE / "platform_prometheus.tf").read_text()
    block = _extract_resource_block(prom, "helm_release", "dcgm_exporter")
    assert 'aws_ecr_repository.vendored["dcgm_exporter"]' in block, "DCGM must pull the vendored ECR image"
    # GPU-nodes ONLY: nodeSelector on the gpu-g label (a tolerate-all DaemonSet crashloops on CPU
    # nodes — no GPU/driver, verified live) + tolerate the GPU taint so it CAN land there.
    assert "nodeSelector.inference/accelerator" in block, "DCGM must nodeSelector onto GPU nodes only"
    assert "nvidia.com/gpu" in block, "DCGM must tolerate the GPU taint"
    # ServiceMonitor must carry the release label the stack's Prometheus selects on, or it's ignored.
    assert "serviceMonitor.enabled" in block, "DCGM must emit a ServiceMonitor for Prometheus"
    assert "serviceMonitor.additionalLabels.release" in block, (
        "DCGM ServiceMonitor must carry release=kube-prometheus-stack or Prometheus ignores it"
    )


def test_grafana_vendored_not_pulled_through() -> None:
    """Grafana has no no-creds registry (docker.io/ghcr only) → it MUST be vendored, not pull-through."""
    images = (ENGINE / "images.tf").read_text()
    assert "docker.io/grafana/grafana" in images, "Grafana must be a vendored_images entry from docker.io"
    prom = (ENGINE / "platform_prometheus.tf").read_text()
    block = _extract_resource_block(prom, "helm_release", "kube_prometheus_stack")
    assert 'aws_ecr_repository.vendored["grafana"]' in block, "Grafana image must resolve to the vendored ECR repo"
    # The vendoring buildspec must NOT use --all (skopeo 1.4.1 chokes on SBOM layers).
    assert "skopeo copy --all" not in images, "vendoring must omit --all (in-toto SBOM layer breaks skopeo 1.4.1)"


def test_container_insights_gated_and_ordered() -> None:
    """CloudWatch Observability addon is gated on enable_container_insights with a CW role."""
    content = (ENGINE / "eks_addons.tf").read_text()
    block = _extract_resource_block(content, "aws_eks_addon", "cw_observability")
    assert "var.enable_container_insights" in block, "CW addon must be gated on the flag"
    assert "amazon-cloudwatch-observability" in block
    assert "module.cw_observability_role" in block, "CW addon must use its pod-identity role"


def test_monitoring_vpc_endpoint_present() -> None:
    """Container Insights pushes CloudWatch METRICS → the 'monitoring' endpoint must exist (no-NAT)."""
    content = (ENGINE / "modules" / "vpc" / "main.tf").read_text()
    assert '"monitoring"' in content, "monitoring VPC endpoint required for Container Insights metrics"


def test_metrics_chart_ships_servicemonitor_and_dashboard() -> None:
    """charts/metrics MUST ship the Karpenter ServiceMonitor + a GPU Grafana dashboard ConfigMap."""
    sm = (TEMPLATE_PATH / "charts" / "metrics" / "templates" / "karpenter-servicemonitor.yaml").read_text()
    assert "kind: ServiceMonitor" in sm and "karpenter" in sm
    dash = (TEMPLATE_PATH / "charts" / "metrics" / "templates" / "gpu-dashboard.yaml").read_text()
    assert "grafanaDashboardLabel" in dash, "dashboard ConfigMap must carry the sidecar discovery label"
    assert "DCGM_FI_DEV_GPU_UTIL" in dash, "GPU dashboard must chart DCGM GPU metrics"


# --- Autoscaling & orchestration operators — KEDA + KRO ---


def test_keda_images_vendored_not_pulled_through() -> None:
    """KEDA's three images are ghcr.io-only → they MUST be vendored, not pinned to a pull-through prefix.

    Verified live: kedacore/keda{,-metrics-apiserver,-admission-webhooks} are NOT
    on Quay (401) or ECR-Public (404); ghcr is the only anonymous home, and ghcr is
    not a no-creds pull-through upstream. So all three are vendored to ECR via
    CodeBuild, like Grafana/DCGM.
    """
    images = (ENGINE / "images.tf").read_text()
    for key, repo in (
        ("keda_operator", "ghcr.io/kedacore/keda"),
        ("keda_metrics_apiserver", "ghcr.io/kedacore/keda-metrics-apiserver"),
        ("keda_admission_webhooks", "ghcr.io/kedacore/keda-admission-webhooks"),
    ):
        assert key in images, f"{key} must be a vendored_images entry"
        assert repo in images, f"{key} source must be the ghcr.io image {repo}"

    block = _extract_resource_block((ENGINE / "platform_keda.tf").read_text(), "helm_release", "keda")
    for key in ("keda_operator", "keda_metrics_apiserver", "keda_admission_webhooks"):
        assert f'aws_ecr_repository.vendored["{key}"]' in block, (
            f"KEDA {key} image must resolve to its vendored ECR repo"
        )
    # None of the three components may fall back to the ghcr chart default.
    code = "\n".join(ln for ln in block.splitlines() if not ln.lstrip().startswith("#"))
    assert "ghcr.io" not in code, "no KEDA image may reference ghcr.io (not a no-creds pull-through upstream)"


def test_keda_on_system_ng_ordered_after_prometheus() -> None:
    """KEDA control-plane pods MUST be on the tainted system NG and ordered after Prometheus.

    KEDA scales on Prometheus queries, so the metrics stack (and its ServiceMonitor
    CRD) must exist first; and its operator/metrics-apiserver/webhook are control-loop
    pods that belong on the fixed system NG, never on a Karpenter GPU node.
    """
    block = _extract_resource_block((ENGINE / "platform_keda.tf").read_text(), "helm_release", "keda")
    assert "system_node_selector" in block and "system_toleration" in block, (
        "KEDA components must be pinned to the tainted system NG"
    )
    for dep in (
        "null_resource.cluster_addons",
        "null_resource.pullthrough_ready",
        "helm_release.kube_prometheus_stack",
        "null_resource.image_vendor",
    ):
        assert dep in block, f"keda.depends_on missing {dep}"


def test_kro_image_and_chart_pinned_to_pullthrough_anonymous() -> None:
    """KRO's chart + controller image come from registry.k8s.io (pull-through), with NO chart-pull auth.

    KRO 0.9.x publishes both the chart (oci://registry.k8s.io/kro/charts/kro) and the
    controller image (registry.k8s.io/kro/kro) to the Kubernetes registry — a no-creds
    pull-through upstream (verified live), so no vendoring. The image is repinned to
    the registry-k8s pull-through URI; the chart pull must NOT carry a token (same
    perpetual-diff / stale-token trap as Karpenter).
    """
    block = _extract_resource_block((ENGINE / "platform_kro.tf").read_text(), "helm_release", "kro")
    assert "registry-k8s/kro/kro" in block, "KRO image must repin to the registry-k8s pull-through URI"
    assert "repository_password" not in block and "repository_username" not in block, (
        "KRO chart pull must be anonymous (perpetual-diff + stale-token trap)"
    )
    assert "oci://registry.k8s.io/kro" in block, "KRO chart must come from the registry.k8s.io OCI repo"


def test_kro_on_system_ng() -> None:
    """The KRO controller MUST be pinned to the tainted system NG — it orchestrates, never on GPU."""
    block = _extract_resource_block((ENGINE / "platform_kro.tf").read_text(), "helm_release", "kro")
    assert "deployment.nodeSelector.inference/role" in block.replace("\\", ""), (
        "KRO must nodeSelector onto the system NG"
    )
    assert "inference/role" in block and "system" in block, "KRO must target inference/role=system"
    for dep in ("null_resource.cluster_addons", "null_resource.pullthrough_ready"):
        assert dep in block, f"kro.depends_on missing {dep}"


def test_kro_starters_ordered_after_both_operators() -> None:
    """The starter ResourceGroups need the KRO CRDs AND KEDA's ScaledObject CRD, so they install last."""
    block = _extract_resource_block((ENGINE / "platform_kro.tf").read_text(), "helm_release", "kro_starters")
    assert "helm_release.kro" in block, "starters need the KRO controller (CRDs) first"
    assert "helm_release.keda" in block, "starters emit a KEDA ScaledObject — KEDA CRD must exist first"


def test_kro_starter_encodes_consumer_contract() -> None:
    """The starter ResourceGroup MUST expand into the full consumer graph with the cluster's conventions.

    Deployment (GPU nodeSelector + taint toleration), Service, KEDA ScaledObject, and
    a ServiceMonitor carrying the release label — the contract that otherwise lives as
    prose in AGENT.md, as code.
    """
    rg = (TEMPLATE_PATH / "charts" / "kro" / "templates" / "inference-deployment-rg.yaml").read_text()
    # KRO 0.9.x uses kind ResourceGraphDefinition + kro.run/v1alpha1.
    assert "kind: ResourceGraphDefinition" in rg and "kro.run/v1alpha1" in rg, (
        "starter must be a kro.run/v1alpha1 ResourceGraphDefinition"
    )
    # The four child resources of the standardized graph.
    for kind in ("kind: Deployment", "kind: Service", "kind: ScaledObject", "kind: ServiceMonitor"):
        assert kind in rg, f"starter graph must include a {kind}"
    # GPU placement baked in so a consumer author can't strand pods Pending.
    assert "inference/accelerator" in rg and "nvidia.com/gpu" in rg, (
        "starter Deployment must carry the GPU nodeSelector + taint toleration"
    )
    # ScaledObject scales on Prometheus (the t0 of the autoscaling loop).
    assert "keda.sh/v1alpha1" in rg and "type: prometheus" in rg, (
        "starter must emit a KEDA ScaledObject with a prometheus trigger"
    )
    # ServiceMonitor must carry the release label or Prometheus ignores it (lesson learned).
    assert "serviceMonitorLabel" in rg, "starter ServiceMonitor must carry the release label"


def test_kro_starters_chart_version_matches_template_version() -> None:
    """charts/kro Chart.yaml version must track the template version (SemVer spelling), like the others."""
    manifest = yaml.safe_load((TEMPLATE_PATH / "manifest.yaml").read_text())
    semver = manifest["template"]["version"].replace("rc", "-rc")
    chart = yaml.safe_load((TEMPLATE_PATH / "charts" / "kro" / "Chart.yaml").read_text())
    assert chart["version"] == semver, (
        f"charts/kro version ({chart['version']}) must equal SemVer of template version ({semver})"
    )


# --- Chart-onboard rehost job ---


def test_onboarder_is_separate_codebuild_project() -> None:
    """Chart-onboard is a SECOND codebuild_job instance, distinct from the platform image_vendor job.

    Different buildspec (chart rehost, not single-image mirror), larger compute (weights
    are 10s-100s of GB), different IAM (workload/* ECR + the shared bucket). Reuses the
    modules/codebuild_job shape.
    """
    content = (ENGINE / "onboarder.tf").read_text()
    block = _extract_resource_block_module(content, "onboarder")
    assert "./modules/codebuild_job" in block, "onboarder must reuse the codebuild_job module"
    # LARGE suffices: the weight copy is server-side (S3 moves the bytes), so the job is
    # neither memory- nor NIC-bound and a bigger tier does not raise S3 bandwidth.
    assert "BUILD_GENERAL1_LARGE" in block, "onboarder compute tier should be LARGE (server-side copy is not IO-bound)"
    # It targets the workload/* ECR prefix, not the platform vendored repos.
    assert "workload_repo_arn" in block, "onboarder must scope pushes to the workload/* ECR prefix"


def test_onboarder_iam_scopes_workload_ecr_and_bucket() -> None:
    """The onboard job's extra IAM grants create+push on workload/* and WRITE the shared bucket
    only; source-bucket READ comes from the AmazonS3ReadOnlyAccess managed policy."""
    content = (ENGINE / "onboarder.tf").read_text()
    doc = _extract_data_block(content, "aws_iam_policy_document", "onboarder_extra")
    assert "ecr:CreateRepository" in doc, "onboard must be able to create workload/* repos (skopeo won't)"
    assert "workload_repo_arn" in doc, "ECR create/push must be scoped to the workload/* prefix ARN"
    assert "s3:PutObject" in doc and "s3:AbortMultipartUpload" in doc, (
        "onboard must write overrides/weights (multipart)"
    )
    assert "module.model_store.bucket_arn" in doc, "S3 write grant must be scoped to the shared bucket ARN"
    assert '"*"' not in doc, "onboard IAM must never use Resource '*' (except the unavoidable ECR auth token)"
    # Reads (any public/JumpStart weight-source bucket) come from the managed policy — a
    # signed GetObject the onboarder needs even for a public bucket. Replaces the old
    # per-bucket onboard_weight_source_buckets allowlist variable (now removed).
    assert "AmazonS3ReadOnlyAccess" in content, "onboarder must attach AmazonS3ReadOnlyAccess for weight-source reads"
    var_content = (ENGINE / "variables.tf").read_text()
    assert 'variable "onboard_weight_source_buckets"' not in var_content, "the per-bucket allowlist var must be removed"


def test_onboarder_workload_repos_are_cluster_scoped_and_tagged() -> None:
    """workload/* ECR repos are created imperatively (not in TF state), so they MUST be
    cluster-scoped (embed resource_name_prefix) to satisfy the two-deployments-coexist rule,
    and MUST carry the deployment tags so they are attributable + reapable by DeploymentId."""
    content = (ENGINE / "onboarder.tf").read_text()
    # Prefix embeds resource_name_prefix (like vendored/* in images.tf), not a bare "workload".
    assert 'workload_repo_prefix = "${local.resource_name_prefix}/workload"' in content, (
        "workload repo prefix must be cluster-scoped via resource_name_prefix"
    )
    # Tags are threaded to the job so ensure_repo can tag created repos.
    assert "RESOURCE_TAGS_JSON = jsonencode(local.combined_tags)" in content, (
        "onboarder must pass the deployment tags to the job for repo tagging"
    )
    doc = _extract_data_block(content, "aws_iam_policy_document", "onboarder_extra")
    assert "ecr:TagResource" in doc, "onboarder must be allowed to tag the repos it creates"
    # onboarder.py must consume the tags into create-repository --tags.
    onboarder_py = (ENGINE / "onboarder.py").read_text()
    assert "RESOURCE_TAGS_JSON" in onboarder_py and "--tags" in onboarder_py, (
        "onboarder.py must tag created repos from RESOURCE_TAGS_JSON"
    )


def test_onboarder_buildspec_runs_the_script_and_publishes_output() -> None:
    """The buildspec must decode+run engine/onboarder.py and publish the emitted artifact."""
    content = (ENGINE / "onboarder.tf").read_text()
    block = _extract_resource_block_module(content, "onboarder")
    # The onboard logic is the shipped module, embedded (single source of truth). It is
    # gzip+base64 (base64gzip): plain base64 exceeds CodeBuild's 25600-char buildspec cap.
    assert "onboarder_script_b64" in block, "buildspec must embed engine/onboarder.py"
    assert 'base64gzip(file("${path.module}/onboarder.py"))' in content, (
        "the embedded module must be gzip+base64 engine/onboarder.py (base64 alone exceeds the buildspec cap)"
    )
    assert "base64 -d | gunzip" in block, "buildspec must gunzip the gzip+base64 module before running it"
    assert "python3 /tmp/onboarder.py" in block, "buildspec must invoke the decoded onboard module"
    # Publishing is mode-agnostic: it sources the result manifest the module writes.
    assert "onboard-result.env" in block, "buildspec must source the module's result manifest"
    assert "REHOST_OUT" in block and "ONBOARD_OUTPUT_BASENAME" in block, (
        "buildspec must publish the emitted artifact (overrides.yaml or graph-air-gapped.yaml) to rehost/out"
    )


def test_onboarder_module_handles_both_paths_and_backstops() -> None:
    """onboarder.py MUST digest-vendor images, support chart + graph paths, and backstop."""
    script = (ENGINE / "onboarder.py").read_text()
    assert "skopeo" in script and "--all" in script, "workload images must be digest-vendored with --all (multi-arch)"
    assert "inspect" in script, "must resolve the immutable source digest"
    assert "helm" in script and "template" in script, "Path-A backstop must render the chart with the overrides"
    assert "BACKSTOP FAILED" in script, "backstop must fail the build when a ref doesn't resolve to our ECR/S3"
    # Auto-detects the two input formats and emits the matching artifact.
    assert "onboard_chart" in script and "onboard_graph" in script, "must support both the chart and graph paths"
    assert "overrides.yaml" in script and "graph-air-gapped.yaml" in script, "must emit the per-mode artifact"
    assert "def detect_mode" in script, "must auto-detect chart (Chart.yaml) vs graph (graph.yaml)"


def test_consumer_consumption_outputs_present() -> None:
    """The consumer-facing outputs must be exported for chart onboarding."""
    content = (ENGINE / "outputs.tf").read_text()
    for out in (
        "ecr_registry",
        "onboarder_codebuild_project",
        "models_s3_uri",
        "onboarder_input_s3_uri",
        "onboarder_output_s3_uri",
        "trusted_upstream_registries",
    ):
        assert f'output "{out}"' in content, f"missing consumer-facing output: {out}"


def test_agent_md_ships_chart_image_helper_and_convention() -> None:
    """AGENT.md.template MUST document the images:/weights: convention + the chart.image helper."""
    content = (TEMPLATE_PATH / "AGENT.md.template").read_text()
    assert 'define "chart.image"' in content, "AGENT.md must ship the chart.image helper verbatim"
    assert "images:" in content and "weights:" in content, "AGENT.md must document the value convention"
    assert "onboarder_codebuild_project" in content, "AGENT.md must show how to start the onboard job"


# --- gpu-p high-end NodePool + polish ---

CHARTS = TEMPLATE_PATH / "charts" / "karpenter" / "templates"


def test_gpu_p_nodepool_is_cost_safe_isolated() -> None:
    """The gpu-p NodePool MUST be gated + carry a DISTINCT taint on top of the shared GPU taint.

    Cost-safety: a pod reaches P only by opting in on BOTH the nvidia-p label
    AND the inference/accelerator=nvidia-p taint toleration. An under-specified GPU pod
    (generic nvidia.com/gpu toleration, no accelerator selector) is repelled from P and
    falls to the cheaper gpu-g pool — so P is never grabbed OR provisioned by accident.
    """
    nodepools = (CHARTS / "nodepools.yaml").read_text()
    # Gated on the enabled flag.
    assert "if .Values.gpuP.enabled" in nodepools, "gpu-p pool must be gated on gpuP.enabled"
    # The gpu-p pool block: label + BOTH taints.
    gpu_p = nodepools[nodepools.index("name: gpu-p") :]
    assert "inference/accelerator: nvidia-p" in gpu_p, "gpu-p nodes must be labeled nvidia-p"
    assert "nvidia.com/gpu" in gpu_p, "gpu-p must carry the shared GPU taint (keeps non-GPU pods off)"
    # DISTINCT tier taint whose KEY differs from the label key — a key that is both a
    # label and a taint breaks Karpenter's scheduling simulation (verified live).
    assert "key: inference/gpu-tier" in gpu_p and 'value: "high"' in gpu_p, (
        "gpu-p must carry a DISTINCT inference/gpu-tier taint so P requires explicit opt-in (cost-safe)"
    )
    assert "key: inference/accelerator" not in gpu_p, (
        "the tier taint key must NOT reuse the label key inference/accelerator "
        "(a key that is both a label and a taint breaks Karpenter scheduling)"
    )
    assert "gpuP.instanceFamilies" in gpu_p, "gpu-p must select the p-family list"
    # The p-families themselves live in values.yaml (the template references them).
    values = (TEMPLATE_PATH / "charts" / "karpenter" / "values.yaml").read_text()
    assert '"p4d"' in values and '"p5"' in values, "values must default gpuP.instanceFamilies to p-families"


def test_gpu_p_nodeclass_shares_gpu_ami_with_odcr_seam() -> None:
    """The gpu-p EC2NodeClass reuses the GPU AMI and exposes the ODCR capacity-reservation seam."""
    nodeclass = (CHARTS / "ec2nodeclass.yaml").read_text()
    assert "if .Values.gpuP.enabled" in nodeclass, "gpu-p EC2NodeClass must be gated"
    gpu_p = nodeclass[nodeclass.index("name: gpu-p") :]
    assert "amiFamily: AL2023" in gpu_p, "gpu-p must use the AL2023 NVIDIA AMI (shared with gpu-g)"
    assert "gpuAmiId" in gpu_p, "gpu-p must select the resolved GPU AMI id"
    assert "httpPutResponseHopLimit: 2" in gpu_p, "gpu-p must set IMDS hop limit 2 (pod creds)"
    # ODCR seam gated on a reservation id being set.
    assert "capacityReservationSelectorTerms" in gpu_p, "gpu-p must expose the ODCR seam"
    assert "if .Values.gpuP.capacityReservationId" in gpu_p, "ODCR seam must be gated on a reservation id"


def test_gpu_p_gate_wired_from_terraform() -> None:
    """enable_gpu_p_nodepool + the ODCR id must be variables wired into the NodePools release."""
    variables = (ENGINE / "variables.tf").read_text()
    assert 'variable "enable_gpu_p_nodepool"' in variables, "gpu-p toggle must be a variable"
    assert 'variable "gpu_p_capacity_reservation_id"' in variables, "ODCR id must be a variable"
    # Variables carry no defaults (defaults live in presets).
    defaults = (ENGINE / "presets" / "defaults-all.tfvars").read_text()
    assert "enable_gpu_p_nodepool" in defaults, "gpu-p toggle must have a preset default"
    # Wired into the karpenter_nodepools helm_release.
    karpenter = (ENGINE / "platform_karpenter.tf").read_text()
    block = _extract_resource_block(karpenter, "helm_release", "karpenter_nodepools")
    assert "gpuP.enabled" in block and "var.enable_gpu_p_nodepool" in block, (
        "the NodePools release must set gpuP.enabled from the variable"
    )
    assert "gpuP.capacityReservationId" in block, "the NodePools release must pass the ODCR id"


def test_cw_observability_manager_pinned_to_system_ng() -> None:
    """The CloudWatch Observability operator (manager) MUST be pinned to the system NG (polish).

    The agent/Fluent Bit DaemonSets stay tolerate-all (collect from every node), but the
    controller-manager Deployment is a control-loop pod — pinning it to the tainted
    system NG keeps it off Karpenter nodes (where it would block consolidation).
    """
    content = (ENGINE / "eks_addons.tf").read_text()
    block = _extract_resource_block(content, "aws_eks_addon", "cw_observability")
    assert "manager" in block, "CW addon config must set the manager (operator) placement"
    assert "system_node_selector" in block and "system_toleration" in block, (
        "the CW manager must be pinned to the tainted system NG"
    )


def test_local_chart_releases_carry_content_hash() -> None:
    """Every first-party local-chart helm_release MUST inject local.chart_hashes[...] as a `set`.

    The helm provider keys a release on its `set` values + chart version, not the chart
    directory's file contents — so without this, editing a chart template/values file
    produces NO plan diff and the old render stays deployed (diagnosed live). The
    content hash makes any chart-file change flip a tracked input → planned upgrade.
    """
    main = (ENGINE / "main.tf").read_text()
    # The hash local must exist and cover every chart dir.
    assert "chart_hashes" in main, "main.tf must define local.chart_hashes"
    chart_dirs = {"karpenter", "kro", "metrics", "storage"}
    declared = set(re.findall(r'"(karpenter|kro|metrics|storage)"', main))
    assert chart_dirs <= declared, f"chart_hashes must cover all local charts, missing {chart_dirs - declared}"

    # Each local-chart helm_release must inject its hash. (file, release, chart key)
    releases = [
        ("platform_karpenter.tf", "karpenter_nodepools", "karpenter"),
        ("platform_kro.tf", "kro_starters", "kro"),
        ("platform_prometheus.tf", "metrics", "metrics"),
        ("platform_storage.tf", "storage", "storage"),
    ]
    for tf_file, release, chart_key in releases:
        block = _extract_resource_block((ENGINE / tf_file).read_text(), "helm_release", release)
        assert f'local.chart_hashes["{chart_key}"]' in block, (
            f'helm_release.{release} must inject local.chart_hashes["{chart_key}"] as a set value '
            "(else chart-file edits don't re-apply)"
        )


def test_agent_md_documents_gpu_tiers_and_seams() -> None:
    """AGENT.md.template MUST document the g/p accelerator tiers + the cost-safety seams."""
    content = (TEMPLATE_PATH / "AGENT.md.template").read_text()
    assert "inference/accelerator: nvidia-p" in content, "must show how to target the P tier"
    assert "inference/gpu-tier" in content, "must document the P cost-safety taint"
    assert "enable_gpu_p_nodepool" in content, "must reference the P pool gate"
    assert "gpu_p_capacity_reservation_id" in content, "must document the ODCR seam"
    for seam in ("ODCR", "Shared GPU pool"):
        assert seam in content, f"must document the {seam} seam"


# --- Multi-node: EFA device plugin ---


def test_efa_registry_inferred_not_hardcoded() -> None:
    """The EFA image's EKS regional registry MUST be inferred from vpc-cni, never hardcoded.

    The EFA plugin lives only on the EKS-managed regional ECR, whose account is
    region-specific. Instead of a region->account map, we read the already-installed
    vpc-cni (aws-node) DaemonSet image and take its <account>.dkr.ecr.<region> prefix —
    whatever EKS resolved for this region/partition. Guards against a regression back
    to a hardcoded account or lookup map.
    """
    images = (ENGINE / "images.tf").read_text()
    # The inference source: the aws-node DaemonSet image prefix.
    assert 'data "kubernetes_resource" "aws_node"' in images, (
        "EFA registry must be inferred from the vpc-cni (aws-node) DaemonSet"
    )
    assert "eks_ecr_registry = " in images and "split(" in images, (
        "eks_ecr_registry must be the split() prefix of the aws-node image, not a literal"
    )
    # No hardcoded EKS account or region->account map may creep back in.
    assert "602401143452" not in images, "the EKS ECR account must never be hardcoded in images.tf"
    assert "eks_ecr_account_by_region" not in images, "no region->account lookup map (that isn't inference)"
    # The read must defer to apply (it needs a live cluster) and be gated on EFA.
    efa_block = images[images.index("efa_vendored_images") :]
    assert "var.enable_efa ?" in efa_block, "EFA vendoring must be gated on enable_efa"
    assert "eks/aws-efa-k8s-device-plugin" in images, "EFA source repo path must be the EKS convention"


def test_efa_image_vendored_and_release_repinned() -> None:
    """EFA is NOT on public.ecr.aws → it MUST be vendored into our ECR and the release repinned."""
    images = (ENGINE / "images.tf").read_text()
    assert "efa_device_plugin" in images, "efa_device_plugin must be a vendored_images entry"
    block = _extract_resource_block((ENGINE / "platform_efa.tf").read_text(), "helm_release", "efa_device_plugin")
    assert 'aws_ecr_repository.vendored["efa_device_plugin"]' in block, (
        "EFA release image.repository must resolve to the vendored ECR repo"
    )
    assert "local.vendored_tag" in block, "EFA release image.tag must be the vendored tag"
    assert "null_resource.image_vendor" in block, "EFA release must depend on the vendor job completing"


# --- Capacity: one source of truth for NodePool limits AND Kueue quota ---


def test_capacity_caps_feed_both_nodepool_limits_and_kueue_quota() -> None:
    """The *_capacity vars are the SINGLE source of truth: same value → NodePool spec.limits
    AND Kueue nominalQuota, so admission (Kueue) can never exceed provisioning (Karpenter).

    Guards against the reviewer's objection to a standalone manual Kueue quota dial: the
    quota is DERIVED from the capacity caps, not set independently.
    """
    karpenter = (ENGINE / "platform_karpenter.tf").read_text()
    kueue = (ENGINE / "platform_kueue.tf").read_text()
    # Each capacity var must feed the Karpenter NodePool limit...
    for cap, chart_key in [
        ("var.gpu_g_capacity", "gpuG.gpuLimit"),
        ("var.gpu_p_capacity", "gpuP.gpuLimit"),
        ("var.cpu_capacity", "cpu.cpuLimit"),
        ("var.memory_capacity", "cpu.memoryLimit"),
    ]:
        assert chart_key in karpenter and cap in karpenter, f"{cap} must set the Karpenter NodePool {chart_key}"
    # ...and the SAME var must feed the Kueue quota (derived, not a separate knob).
    assert "gpuGQuota" in kueue and "var.gpu_g_capacity" in kueue, "Kueue gpuGQuota must derive from gpu_g_capacity"
    assert "gpuQuota" in kueue and "var.gpu_p_capacity" in kueue, "Kueue gpuQuota must derive from gpu_p_capacity"
    assert "cpuQuota" in kueue and "var.cpu_capacity" in kueue, "Kueue cpuQuota must derive from cpu_capacity"
    assert "memoryQuota" in kueue and "var.memory_capacity" in kueue, (
        "Kueue memoryQuota must derive from memory_capacity"
    )
    # The redundant manual quota vars the reviewer objected to must be gone.
    variables = (ENGINE / "variables.tf").read_text()
    for dead in ("kueue_gpu_g_quota", "kueue_gpu_quota", "kueue_efa_quota", "kueue_cpu_quota", "kueue_memory_quota"):
        assert f'variable "{dead}"' not in variables, f"the manual quota var {dead} must be removed (derived now)"


def test_kueue_efa_quota_derived_from_gpu_quota() -> None:
    """EFA nominalQuota is NOT a separate dial — it equals the flavor's GPU quota (a pod needs
    a GPU to use EFA and a node carries ≤1 EFA, so GPU is the binding constraint)."""
    cfg = (TEMPLATE_PATH / "charts" / "kueue" / "templates" / "kueue-config.yaml").read_text()
    assert ".Values.efaQuota" not in cfg, "EFA must not use a standalone efaQuota value"
    # Both flavors set the EFA nominalQuota from the same key as their GPU nominalQuota.
    assert cfg.count("{{ .Values.gpuGQuota | quote }}") == 2, "gpu-g flavor: GPU and EFA quota both from gpuGQuota"
    assert cfg.count("{{ .Values.gpuQuota | quote }}") == 2, "gpu-p flavor: GPU and EFA quota both from gpuQuota"


def test_workload_namespace_decoupled_from_kueue_config_chart() -> None:
    """The inference workload namespace MUST be owned by the engine (ungated), not the
    kueue-config chart — else `helm uninstall kueue-config` cascade-deletes the namespace
    and every running inference workload in it. The chart must not declare a Namespace;
    the engine must own it (platform_workloads.tf) and the release must depend on it."""
    cfg = (TEMPLATE_PATH / "charts" / "kueue" / "templates" / "kueue-config.yaml").read_text()
    assert "kind: Namespace" not in cfg, (
        "kueue-config chart must NOT create the workload namespace (uninstall would delete workloads)"
    )
    workloads_tf = (ENGINE / "platform_workloads.tf").read_text()
    assert 'resource "kubernetes_namespace_v1" "workload"' in workloads_tf, (
        "the workload namespace must be an engine-owned kubernetes_namespace_v1 in platform_workloads.tf"
    )
    # Ungated: it must NOT be gated on enable_kueue (it outlives optional operators).
    ns_block = _extract_resource_block(workloads_tf, "kubernetes_namespace_v1", "workload")
    assert "count" not in ns_block, "the workload namespace must be ungated (no count = var.enable_kueue)"
    block = _extract_resource_block((ENGINE / "platform_kueue.tf").read_text(), "helm_release", "kueue_config")
    assert "kubernetes_namespace_v1.workload" in block, (
        "kueue_config release must depend_on kubernetes_namespace_v1.workload so the LocalQueue's namespace exists"
    )


def test_cluster_autoscaler_discovery_and_scoped_role() -> None:
    """CA discovery tags go on the ASG (MNG tags don't propagate), it balances node groups, and its
    mutating autoscaling actions are tag-scoped to this cluster via Pod Identity."""
    tf = (ENGINE / "platform_cluster_autoscaler.tf").read_text()
    assert 'resource "aws_autoscaling_group_tag"' in tf and "module.node_group.autoscaling_group_name" in tf
    assert "k8s.io/cluster-autoscaler/enabled" in tf
    out = (ENGINE / "modules" / "node_group" / "outputs.tf").read_text()
    assert "autoscaling_group_name" in out and "resources[0].autoscaling_groups[0].name" in out
    ca = _extract_resource_block(tf, "helm_release", "cluster_autoscaler")
    assert "balance-similar-node-groups" in ca
    assoc = _extract_resource_block(tf, "aws_eks_pod_identity_association", "cluster_autoscaler")
    assert "module.cluster_autoscaler_role.role_arn" in assoc
    assert "autoscaling:SetDesiredCapacity" in tf and "autoscaling:TerminateInstanceInAutoScalingGroup" in tf
    assert "k8s.io/cluster-autoscaler/" in tf, "mutating ASG actions must be tag-scoped to this cluster"


def test_control_loop_operators_on_system_ng_and_ha() -> None:
    """Leader-elected operators MUST pin to the tainted system NG AND run 2 replicas (warm standby).

    Placement keeps control-loop pods off Karpenter nodes (where they'd block consolidation);
    2 replicas keep the loop alive across a system-NG node drain. Only proves the .tf SETS the
    keys — that the chart HONORS them is covered by the live test_platform_placement.
    """
    flat = [
        ("platform_karpenter.tf", "karpenter", "inference/role", r'"replicas"\s*,?\s*value\s*=\s*"2"'),
        (
            "platform_cluster_autoscaler.tf",
            "cluster_autoscaler",
            "inference/role",
            r'"replicaCount"\s*,?\s*value\s*=\s*"2"',
        ),
        ("platform_kro.tf", "kro", "inference/role", r'"deployment.replicaCount"\s*,?\s*value\s*=\s*"2"'),
    ]
    for tf_file, release, placement, replica_re in flat:
        block = _extract_resource_block((ENGINE / tf_file).read_text(), "helm_release", release)
        assert placement in block, f"{release} must pin to the system NG ({placement})"
        assert re.search(replica_re, block), f"{release} must set 2 replicas"

    # KEDA passes a nested values doc; operator + metrics-apiserver are HA, webhooks (stateless) are not.
    keda = _extract_resource_block((ENGINE / "platform_keda.tf").read_text(), "helm_release", "keda")
    assert "system_node_selector" in keda and "system_toleration" in keda
    assert re.search(r"operator\s*=\s*\{\s*replicaCount\s*=\s*2", keda)
    assert re.search(r"metricsServer\s*=\s*\{\s*replicaCount\s*=\s*2", keda)

    # Prometheus: memory-limited singleton on the system NG (no HA — StatefulSet).
    prom = _extract_resource_block(
        (ENGINE / "platform_prometheus.tf").read_text(), "helm_release", "kube_prometheus_stack"
    )
    assert "system_node_selector" in prom and "prometheus_memory_limit" in prom
