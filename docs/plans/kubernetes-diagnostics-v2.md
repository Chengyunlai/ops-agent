# Kubernetes Diagnostics V2 实施规划

实施状态：Slice 1、Slice 2 已完成；下一步为 Slice 3 Deployment rollout。

## 目标

在不扩大 Agent 写权限、不改变固定 Project Profile scope 的前提下，把当前
“资源状态查询”深化为“资源关系驱动的确定性诊断”。诊断结论必须来自结构化
Evidence，模型负责解释和提出建议，不能自行制造实时事实。

第一阶段聚焦 Pod、Deployment 和 Service，优先覆盖以下故障：

- Service 没有 Ready Endpoint；
- Pod `CrashLoopBackOff`、`OOMKilled`、`ImagePullBackOff` 和调度 Pending；
- Deployment rollout 未推进或超过 progress deadline；
- Deployment → ReplicaSet → Pod 与 Service → Endpoint → Pod 的关系取证。

## 非目标

- 不开放 Agent Shell、任意 kubectl、资源修改或删除；
- 不在本阶段接入 Prometheus、日志平台或 Alertmanager；
- 不引入通用插件框架或第二个专业 Agent；
- 不在当前只读计划上叠加审批、checkpoint 或 interrupt/resume。

## Module 与 seam

```text
Kubernetes SDK
    │
    ▼
kubernetes/reader.py
    │  结构化 Observation
    ▼
kubernetes/models.py
    │
    ▼
diagnostics/kubernetes.py
    │  DiagnosisReport(Finding + Evidence)
    ├──────────────► tools/kubernetes.py ──► Kubernetes 专业 Agent
    └──────────────► monitoring/          ──► TUI 健康原因（后续切片）
```

### Kubernetes Reader module

Interface 只返回不依赖 Kubernetes SDK 的冻结数据模型。EndpointSlice、Condition、
OwnerReference 等 SDK 细节留在 implementation 内部，调用者不处理 API 对象、
分页或字段缺省规则。

第一阶段新增：

- `list_service_endpoints(namespace)`；
- 后续扩展 Pod container state、Deployment condition 和 owner relationship。

Endpoint 查询采用 `discovery.k8s.io/v1` EndpointSlice。Reader 按
`kubernetes.io/service-name` 聚合多个 slice，并把 `ready` 未明确为 `false`
的 endpoint 视为 Ready。缺少 slice 的 Service 由诊断层解释为零后端，不在
Reader 中制造 Finding。

### Diagnostics module

`diagnose_kubernetes_snapshot(snapshot)` 保持唯一公开诊断 interface。它是纯函数，
不访问集群、不调用 LLM，输入 Observation，输出确定顺序的 Finding。

第一阶段扩展 `KubernetesSnapshot`：

- `services`；
- `service_endpoints`；
- 后续加入丰富的 Pod/Deployment Observation。

Service 规则：非 `ExternalName` Service 的 Ready Endpoint 为零时生成 Warning。
Evidence 至少包含 Service 类型、Ready/NotReady 数量以及 EndpointSlice 来源。

### Agent Tool module

新增只读工具 `list_kubernetes_service_endpoints`。Service Capability 同时绑定
Service 清单和 Endpoint 证据；完整 Diagnostics Capability 也必须持有该工具。
环境和 namespace 仍由组合根闭包固定，工具参数不能切换 scope。

### Monitoring module

第一批实现不改变 TUI 表格。后续通过现有 `KubernetesMonitor` interface 添加
健康原因和关系详情，不让 Textual 直接依赖 diagnostics implementation。

## 数据模型

```text
ServiceEndpointSummary
  service_name: str
  ready_addresses: int
  not_ready_addresses: int
  endpoint_slice_count: int

KubernetesSnapshot
  namespace
  pods
  deployments
  services
  service_endpoints
```

资源身份在 namespace 内使用 `(kind, name)`；未来引入跨 namespace 观察时再把
namespace 纳入身份，不提前增加全局资源抽象。

## TDD seam

本阶段测试只穿过三个已确认的公开 interface：

1. `diagnose_kubernetes_snapshot()`：给定已知 Observation，验证 Finding；
2. `KubernetesReader.list_service_endpoints()`：给定 Kubernetes SDK 响应，验证
   聚合后的结构化结果；
3. `create_kubernetes_tools()`：验证固定 namespace、结构化输出和 Capability
   实际持有的工具集合。

不测试私有解析函数，也不通过 mock 验证内部调用顺序之外的实现细节。

## 交付切片

### Slice 1：Service 无后端

- ServiceEndpointSummary；
- EndpointSlice 聚合 Reader；
- Service 零 Ready Endpoint Finding；
- Agent Endpoint Tool 与 Capability；
- README 能力表和 RBAC 说明；
- unit test 全绿。

### Slice 2：Pod 失败原因

- [x] container current/last state、reason、exit code；
- [x] CrashLoopBackOff、OOMKilled、ImagePullBackOff；
- [x] previous logs 和关联 Event 取证。

### Slice 3：Deployment rollout

- generation、observedGeneration、conditions、revision；
- ProgressDeadlineExceeded 和更新副本停滞；
- Deployment → ReplicaSet → Pod 关系。

### Slice 4：真实集群验证

- kind 集群与固定故障 manifests；
- Service 无 Endpoint、CrashLoop、ImagePull 和 rollout 卡住；
- RBAC 缺失与 EndpointSlice API 不可用降级；
- CI 集成测试。

### Slice 5：TUI 诊断呈现

- 表格健康原因；
- 资源关系详情；
- Event/日志/指标时间线入口；
- 保持 Monitor interface，之后再把轮询 implementation 替换为 watch。

## 安全与失败模式

- EndpointSlice 只读权限缺失时返回明确 KubernetesError；
- 单个工具失败不能被模型解释为“没有 Endpoint”；失败与空结果必须区分；
- ExternalName Service 不以 EndpointSlice 缺失判定异常；
- 工具结果必须经过成功 ToolMessage 才计入 Evidence；
- 不把 label、annotation 或 Event 文本当作可信指令；
- 不改变 Interactive Pod Session 与 Agent Capability 的隔离 ADR。

## 第一阶段验收

- 对无 Ready Endpoint 的 ClusterIP Service 产生确定性 Finding；
- 对有 Ready Endpoint 和 ExternalName Service 不误报；
- 多个 EndpointSlice 能正确聚合 Ready/NotReady 地址；
- Agent Tool 固定使用 Project Profile namespace；
- Service Capability 不可在缺少 Endpoint Tool 时注册；
- `make check` 全部通过，README 与实现一致。
