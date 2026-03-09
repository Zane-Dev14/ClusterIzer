"""Error signature pattern matching for crashloop root cause analysis."""

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Callable, Dict, Any

logger = logging.getLogger(__name__)


@dataclass
class FixStep:
    """A single step in a fix plan with command and expected result."""

    step_number: int
    description: str
    command: Optional[str] = None
    expected_result: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        result = {
            "step_number": self.step_number,
            "description": self.description,
        }
        if self.command:
            result["command"] = self.command
        if self.expected_result:
            result["expected_result"] = self.expected_result
        return result


@dataclass
class DiagnosisResult:
    """Complete diagnosis result with root cause and fix plan."""

    type: str  # Signature type (e.g., "nginx_lua_init_fail")
    root_cause: str  # Human-readable explanation
    confidence: float  # 0.0 to 1.0
    evidence: str  # Log excerpt showing the error
    recommended_fix: Optional[str] = None  # Direct actionable fix command/instruction
    fix_plan: List[FixStep] = field(default_factory=list)
    verification_commands: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization and structured usage."""
        return {
            "type": self.type,
            "root_cause": self.root_cause,
            "confidence": self.confidence,
            "evidence": self.evidence,
            "recommended_fix": self.recommended_fix,
            "fix_plan": {
                "commands": [step.command for step in self.fix_plan if step.command],
                "verification": self.verification_commands,
                "steps": [step.to_dict() for step in self.fix_plan],
            },
            "verification_commands": self.verification_commands,
        }


@dataclass
class ErrorSignature:
    """
    Error signature for pattern matching against crash logs.

    Each signature defines:
    - name: Unique identifier
    - patterns: List of regex patterns to match
    - severity: Error severity level
    - root_cause_template: Explanation of the root cause
    - fix_plan_generator: Function that generates contextual fix steps
    """

    name: str
    patterns: List[str]
    severity: str
    root_cause_template: str
    fix_plan_generator: Callable[[str, str, str, str], List[FixStep]]


def _generate_nginx_lua_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for Nginx Lua VM initialization failure."""
    # Extract line number from evidence if present
    line_match = re.search(r"nginx\.conf:(\d+)", evidence)
    line_number = line_match.group(1) if line_match else "unknown"

    steps = [
        FixStep(
            step_number=1,
            description=f"Inspect nginx.conf around line {line_number} to identify the Lua module reference",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- cat /usr/local/openresty/nginx/conf/nginx.conf | sed -n '{max(1, int(line_number) - 10)},{int(line_number) + 10}p'"
            if line_number != "unknown"
            else f"kubectl exec -n {namespace} {pod_name} -c {container} -- cat /usr/local/openresty/nginx/conf/nginx.conf | grep -A10 -B10 lua_",
            expected_result="Identify which Lua module or script is being loaded (e.g., require_content_by_lua, lua_package_path)",
        ),
        FixStep(
            step_number=2,
            description="Verify Lua module files exist in the container filesystem",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- ls -la /usr/local/openresty/lualib/ /usr/local/openresty/site/lualib/",
            expected_result="Check if required .lua files are present in the expected directories",
        ),
        FixStep(
            step_number=3,
            description="Check lua_package_path configuration in nginx.conf",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- grep -A5 lua_package_path /usr/local/openresty/nginx/conf/nginx.conf",
            expected_result="Verify lua_package_path includes the directory containing the Lua modules",
        ),
        FixStep(
            step_number=4,
            description="Review ConfigMap or volume mounts that may override nginx.conf",
            command=f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.volumes[*]}}' | grep -i config",
            expected_result="Identify if ConfigMap is mounting nginx.conf and may need updating",
        ),
        FixStep(
            step_number=5,
            description="If modules are missing, rebuild the container image with required Lua modules installed",
            command="# Add to Dockerfile: RUN luarocks install <missing-module-name>",
            expected_result="Container image includes all required Lua modules in /usr/local/openresty/lualib/",
        ),
    ]
    return steps


def _generate_oom_killed_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for OOMKilled errors."""
    return [
        FixStep(
            step_number=1,
            description="Check current memory limits and actual usage",
            command=f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.containers[?(@.name==\"{container}\")].resources.limits.memory}}'",
            expected_result="Current memory limit (e.g., 128Mi, 512Mi)",
        ),
        FixStep(
            step_number=2,
            description="Review historical memory usage to determine appropriate limit",
            command=f"kubectl top pod {pod_name} -n {namespace} --containers",
            expected_result="Identify memory usage patterns before OOMKill",
        ),
        FixStep(
            step_number=3,
            description="Update deployment to increase memory limits",
            command=f"kubectl set resources deployment/<deployment-name> -n {namespace} -c {container} --limits=memory=1Gi",
            expected_result="Memory limit increased to accommodate workload requirements",
        ),
        FixStep(
            step_number=4,
            description="Verify the application doesn't have a memory leak",
            command=f"kubectl logs {pod_name} -n {namespace} -c {container} | grep -i 'memory\\|heap\\|allocation'",
            expected_result="Check for excessive memory allocation or leak indicators",
        ),
    ]


def _generate_permission_denied_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for permission denied errors."""
    # Extract the path/resource that was denied
    path_match = re.search(r"permission denied[:\s]+([^\s]+)", evidence, re.IGNORECASE)
    denied_path = path_match.group(1) if path_match else "/path/to/resource"

    return [
        FixStep(
            step_number=1,
            description="Check current securityContext for the container",
            command=f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.containers[?(@.name==\"{container}\")].securityContext}}'",
            expected_result="View current runAsUser, runAsGroup, fsGroup settings",
        ),
        FixStep(
            step_number=2,
            description="Verify file ownership and permissions in the container",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- ls -la {denied_path}",
            expected_result="Identify file owner/group and permission bits",
        ),
        FixStep(
            step_number=3,
            description="Update deployment with appropriate securityContext",
            command="# Add to deployment spec:\n# securityContext:\n#   runAsUser: 1000\n#   fsGroup: 1000",
            expected_result="Container runs with user/group that has access to required files",
        ),
        FixStep(
            step_number=4,
            description="If Docker socket access needed, consider security implications",
            command="# For /var/run/docker.sock: mount socket as volume AND add user to docker group",
            expected_result="Evaluate if privileged access is truly necessary (security risk)",
        ),
    ]


def _generate_address_in_use_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for address already in use errors."""
    # Extract port number if present
    port_match = re.search(r":(\d+)", evidence)
    port = port_match.group(1) if port_match else "unknown"

    return [
        FixStep(
            step_number=1,
            description=f"Check if multiple containers are trying to bind to port {port}",
            command=f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.spec.containers[*].ports[*].containerPort}}'",
            expected_result="List all container ports to identify conflicts",
        ),
        FixStep(
            step_number=2,
            description="Check if another pod or process is already using the port",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- netstat -tlnp | grep {port}",
            expected_result="Identify process currently bound to the port",
        ),
        FixStep(
            step_number=3,
            description="Review deployment for duplicate port configurations",
            command=f"kubectl get deployment -n {namespace} -o yaml | grep -A5 containerPort",
            expected_result="Ensure each container has unique port assignments",
        ),
        FixStep(
            step_number=4,
            description="Update port configuration to use an available port",
            command=f"# Update deployment to change containerPort: {port} to an unused port",
            expected_result="Container binds successfully without port conflicts",
        ),
    ]


def _generate_module_not_found_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for module not found / import errors."""
    # Extract module name if present
    module_match = re.search(
        r"(?:import|module)\s+['\"]?([a-zA-Z0-9_\.]+)", evidence, re.IGNORECASE
    )
    module_name = module_match.group(1) if module_match else "unknown"

    return [
        FixStep(
            step_number=1,
            description=f"Check if module '{module_name}' is installed in the container",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- pip list | grep -i {module_name}"
            if module_name != "unknown"
            else f"kubectl exec -n {namespace} {pod_name} -c {container} -- pip list",
            expected_result="Verify if the Python package is present",
        ),
        FixStep(
            step_number=2,
            description="Check application requirements.txt or package.json",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- cat requirements.txt || cat package.json",
            expected_result="Ensure the module is listed in dependencies",
        ),
        FixStep(
            step_number=3,
            description="Rebuild container image with missing module",
            command=f"# Add to Dockerfile: RUN pip install {module_name} (or npm install {module_name})",
            expected_result="Container image includes the required module",
        ),
        FixStep(
            step_number=4,
            description="Verify PYTHONPATH or NODE_PATH environment variables",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- env | grep PATH",
            expected_result="Module path is included in the application's search path",
        ),
    ]


def _generate_connection_refused_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for connection refused errors."""
    # Extract host/port if present
    host_match = re.search(
        r"(?:to|connect)\s+([a-zA-Z0-9\-\.]+):(\d+)", evidence, re.IGNORECASE
    )
    host = host_match.group(1) if host_match else "unknown"
    port = host_match.group(2) if host_match else "unknown"

    return [
        FixStep(
            step_number=1,
            description=f"Verify the target service {host} is running",
            command=f"kubectl get svc -n {namespace} | grep {host}"
            if host != "unknown"
            else f"kubectl get svc -n {namespace}",
            expected_result="Target service exists and has endpoints",
        ),
        FixStep(
            step_number=2,
            description="Check if target service has healthy pods",
            command=f"kubectl get endpoints {host} -n {namespace}",
            expected_result="Service has at least one ready endpoint (IP:port)",
        ),
        FixStep(
            step_number=3,
            description="Test connectivity from the failing pod",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- nc -zv {host} {port}"
            if host != "unknown" and port != "unknown"
            else f"kubectl exec -n {namespace} {pod_name} -c {container} -- netstat -an",
            expected_result="Connection succeeds or provides more specific error",
        ),
        FixStep(
            step_number=4,
            description="Review NetworkPolicy that may be blocking traffic",
            command=f"kubectl get networkpolicy -n {namespace}",
            expected_result="Ensure no NetworkPolicy is blocking pod-to-pod or pod-to-service communication",
        ),
    ]


def _generate_database_unavailable_fix_plan(
    pod_name: str, namespace: str, container: str, evidence: str
) -> List[FixStep]:
    """Generate fix plan for database connection errors."""
    # Extract database type if present
    db_type = "database"
    if "postgres" in evidence.lower():
        db_type = "PostgreSQL"
    elif "mysql" in evidence.lower():
        db_type = "MySQL"
    elif "mongo" in evidence.lower():
        db_type = "MongoDB"

    return [
        FixStep(
            step_number=1,
            description=f"Verify {db_type} service is running",
            command=f"kubectl get pods -n {namespace} -l app={db_type.lower()}",
            expected_result="Database pod is in Running state with 1/1 ready",
        ),
        FixStep(
            step_number=2,
            description="Check database service endpoints",
            command=f"kubectl get endpoints -n {namespace} | grep -i {db_type.lower()}",
            expected_result="Database service has active endpoints",
        ),
        FixStep(
            step_number=3,
            description="Verify database connection credentials in secrets",
            command=f"kubectl get secret -n {namespace} | grep -i db",
            expected_result="Database secret exists and is mounted to the pod",
        ),
        FixStep(
            step_number=4,
            description="Test database connectivity from the application pod",
            command=f"kubectl exec -n {namespace} {pod_name} -c {container} -- env | grep -i 'DB\\|DATABASE'",
            expected_result="Verify database connection environment variables are set correctly",
        ),
        FixStep(
            step_number=5,
            description="Check database logs for connection issues",
            command=f"kubectl logs -n {namespace} -l app={db_type.lower()} --tail=50",
            expected_result="Database logs show successful startup and listening on correct port",
        ),
    ]


# Define all error signatures
ERROR_SIGNATURES = [
    ErrorSignature(
        name="nginx_lua_init_fail",
        patterns=[
            r"failed to initialize Lua VM",
            r"lua_load_resty_core failed",
            r"failed to load module ['\"]resty\.",
        ],
        severity="high",
        root_cause_template=(
            "Nginx OpenResty failed to initialize the Lua VM. This typically indicates "
            "a missing Lua module, incorrect lua_package_path configuration, or a broken "
            "nginx configuration referencing unavailable Lua scripts. The Lua runtime is "
            "required for OpenResty to process requests with lua_* directives."
        ),
        fix_plan_generator=_generate_nginx_lua_fix_plan,
    ),
    ErrorSignature(
        name="oom_killed",
        patterns=[
            r"OOMKilled",
            r"out of memory",
            r"memory limit exceeded",
            r"oom.*kill",
        ],
        severity="critical",
        root_cause_template=(
            "Container was killed due to Out Of Memory (OOM). The container exceeded its "
            "memory limit and the kernel terminated it. This can happen due to insufficient "
            "memory limits, memory leaks, or workload spikes requiring more memory than allocated."
        ),
        fix_plan_generator=_generate_oom_killed_fix_plan,
    ),
    ErrorSignature(
        name="permission_denied",
        patterns=[
            r"permission denied",
            r"cannot access.*permission",
            r"EACCES",
            r"access denied",
        ],
        severity="medium",
        root_cause_template=(
            "Container process lacks permissions to access a required file, directory, or resource. "
            "This is typically due to incorrect securityContext settings (runAsUser, fsGroup), "
            "file ownership mismatches, or attempting to access privileged resources without proper configuration."
        ),
        fix_plan_generator=_generate_permission_denied_fix_plan,
    ),
    ErrorSignature(
        name="address_already_in_use",
        patterns=[
            r"address already in use",
            r"bind.*failed.*address",
            r"EADDRINUSE",
            r"port.*already.*use",
        ],
        severity="medium",
        root_cause_template=(
            "Application failed to bind to a network port because another process is already using it. "
            "This can occur due to multiple containers in the same pod trying to use the same port, "
            "port conflicts in the deployment configuration, or leftover processes from previous crashes."
        ),
        fix_plan_generator=_generate_address_in_use_fix_plan,
    ),
    ErrorSignature(
        name="module_not_found",
        patterns=[
            r"ModuleNotFoundError",
            r"ImportError",
            r"cannot import",
            r"Cannot find module",
            r"MODULE_NOT_FOUND",
        ],
        severity="high",
        root_cause_template=(
            "Application failed to import a required module or package. The module is either not "
            "installed in the container image, not listed in requirements.txt/package.json, or the "
            "module path is incorrect. This indicates incomplete dependency installation during image build."
        ),
        fix_plan_generator=_generate_module_not_found_fix_plan,
    ),
    ErrorSignature(
        name="connection_refused",
        patterns=[
            r"connection refused",
            r"dial tcp.*refused",
            r"ECONNREFUSED",
            r"connect.*refused",
        ],
        severity="high",
        root_cause_template=(
            "Application failed to connect to a required service (database, API, cache). The target "
            "service may not be running, may not have ready endpoints, or network policies may be "
            "blocking traffic. This indicates a service dependency issue or network configuration problem."
        ),
        fix_plan_generator=_generate_connection_refused_fix_plan,
    ),
    ErrorSignature(
        name="database_unavailable",
        patterns=[
            r"could not connect to.*database",
            r"postgres.*unavailable",
            r"mysql.*connection.*failed",
            r"mongo.*connection.*refused",
            r"database.*timeout",
        ],
        severity="critical",
        root_cause_template=(
            "Application cannot establish a connection to the database. The database service may not "
            "be running, may not have ready pods, credentials may be incorrect, or the database may "
            "be overwhelmed and rejecting connections. This prevents the application from persisting or reading data."
        ),
        fix_plan_generator=_generate_database_unavailable_fix_plan,
    ),
]


def _get_recommended_fix(
    signature_type: str, pod_name: str, namespace: str, container: str, evidence: str
) -> Optional[str]:
    """Generate recommended actionable fix based on signature type - DIRECT, EXECUTABLE FIXES ONLY."""
    if signature_type == "nginx_lua_init_fail":
        # Extract deployment name from pod_name (format: deployment-hash-pod-id)
        # e.g., "media-frontend-64dd9f988-2nkmd" -> "media-frontend"
        deployment_name = pod_name.rsplit("-", 2)[0] if "-" in pod_name else pod_name
        return (
            f"1. Update Dockerfile: add `RUN luarocks install lfs lua-cjson` to the OpenResty container build\n"
            f"2. Rebuild and push image\n"
            f"3. Redeploy: kubectl rollout restart deployment {deployment_name} -n {namespace}\n"
            f"4. Verify: kubectl get pod -n {namespace} -l app={deployment_name} -o wide"
        )
    elif signature_type == "oom_killed":
        return f"kubectl set resources deployment -n {namespace} --limits=memory=2Gi --requests=memory=1Gi"
    elif signature_type == "permission_denied":
        json_patch = '[{"op": "add", "path": "/spec/template/spec/securityContext", "value":{"runAsUser": 1000, "fsGroup": 1000}}]'
        return (
            f"kubectl patch deployment -n {namespace} --type='json' -p='{json_patch}'"
        )
    elif signature_type == "address_in_use":
        return f"Update containerPort in deployment spec to use an available port, then: kubectl rollout restart deployment -n {namespace}"
    elif signature_type == "module_not_found":
        return (
            "1. Identify missing module from error message\n"
            "2. Add to Dockerfile: RUN pip install <module-name> (Python) or RUN npm install <module-name> (Node.js)\n"
            "3. Rebuild image and redeploy"
        )
    elif signature_type == "connection_refused":
        return (
            f"1. Verify service is running: kubectl get pod -n {namespace} -o wide\n"
            f"2. Check endpoints: kubectl get endpoints -n {namespace}\n"
            f"3. If service not running, scale deployment: kubectl scale deployment <service-name> --replicas=1 -n {namespace}"
        )
    elif signature_type == "database_unavailable":
        return (
            f"1. Check database pod status: kubectl get pod -n {namespace} -l app=database\n"
            f"2. Verify credentials secret exists: kubectl get secret -n {namespace} <db-secret-name>\n"
            f"3. If pod not running, scale it: kubectl scale deployment <database-deployment> --replicas=1 -n {namespace}"
        )
    return None


def diagnose_crash_logs(
    log_text: str,
    pod_name: str,
    namespace: str,
    container: str,
) -> Optional[DiagnosisResult]:
    """
    Analyze crash logs and attempt to diagnose the root cause using error signatures.

    Args:
        log_text: Log output from the crashed container
        pod_name: Name of the pod
        namespace: Namespace of the pod
        container: Container name

    Returns:
        DiagnosisResult with root cause and fix plan, or None if no signature matches
    """
    if not log_text or not log_text.strip():
        logger.debug(f"Empty log text for {namespace}/{pod_name}/{container}")
        return None

    matched_signatures = []

    # Try each signature
    for signature in ERROR_SIGNATURES:
        for pattern in signature.patterns:
            match = re.search(pattern, log_text, re.IGNORECASE | re.MULTILINE)
            if match:
                # Extract evidence (matched line plus context)
                evidence_start = max(0, match.start() - 100)
                evidence_end = min(len(log_text), match.end() + 100)
                evidence = log_text[evidence_start:evidence_end].strip()

                # Limit evidence length
                if len(evidence) > 300:
                    evidence = evidence[:150] + "..." + evidence[-150:]

                matched_signatures.append((signature, evidence))
                break  # Only one match per signature needed

    if not matched_signatures:
        logger.debug(
            f"No error signature matched for {namespace}/{pod_name}/{container}"
        )
        return None

    # Use the first matched signature (could be enhanced to rank by severity)
    signature, evidence = matched_signatures[0]

    # Generate fix plan
    fix_plan = signature.fix_plan_generator(pod_name, namespace, container, evidence)

    # Calculate confidence (higher if multiple signatures match)
    base_confidence = 0.90
    if len(matched_signatures) > 1:
        base_confidence = 0.95

    # Generate verification commands
    verification_commands = [
        f"kubectl get pod {pod_name} -n {namespace} -o jsonpath='{{.status.containerStatuses[?(@.name==\"{container}\")].state}}'",
        f"kubectl describe pod {pod_name} -n {namespace}",
    ]

    result = DiagnosisResult(
        type=signature.name,
        root_cause=signature.root_cause_template,
        confidence=base_confidence,
        evidence=evidence,
        recommended_fix=_get_recommended_fix(
            signature.name, pod_name, namespace, container, evidence
        ),
        fix_plan=fix_plan,
        verification_commands=verification_commands,
    )

    logger.info(
        f"Diagnosed {signature.name} for {namespace}/{pod_name}/{container} "
        f"with {base_confidence * 100:.0f}% confidence"
    )

    return result
