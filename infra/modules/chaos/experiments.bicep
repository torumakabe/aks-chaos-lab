@description('Deployment location')
param location string

@description('Tags object')
param tags object = {}

@description('Target AKS cluster resource ID')
param aksId string

@description('Target namespace for Chaos Mesh selectors')
param namespace string = 'chaos-lab'

@description('Target workload label for selectors (e.g., app=chaos-app)')
param appLabel string = 'chaos-app'

@description('Default step duration in ISO8601 (e.g., PT5M) - FALLBACK for continuous experiments when jsonSpec duration is prioritized')
param defaultDuration string = 'PT5M'

@description('Chaos Mesh duration in seconds (for jsonSpec compatibility)')
// VERIFIED: jsonSpec duration takes PRIORITY over action-level duration
// Azure Chaos Studio uses jsonSpec duration when provided, action-level duration as fallback only
// For consistent behavior, always specify meshDuration in jsonSpec for continuous experiments
param meshDuration string = '300s'

@description('Enable PodChaos experiment')
param enablePodChaos bool = true

@description('Enable NetworkChaos experiment')
param enableNetworkChaos bool = true

@description('Enable NetworkChaos loss (100%) experiment')
param enableNetworkChaosLoss bool = true

@description('Enable StressChaos experiment')
param enableStressChaos bool = true

@description('Enable IOChaos experiment')
param enableIOChaos bool = true

@description('Enable TimeChaos experiment')
param enableTimeChaos bool = true

@description('Enable KernelChaos experiment')
param enableKernelChaos bool = false

@description('Enable HTTPChaos experiment')
param enableHTTPChaos bool = true

@description('Enable DNSChaos experiment')
param enableDNSChaos bool = true

@description('List of FQDNs or wildcard patterns for DNSChaos (e.g., example.com, *.example.com). Chaos Mesh supports glob-style wildcards; include multiple entries as needed.')
param redisHosts array = []

// Create Chaos Studio target for AKS cluster (required for Chaos Mesh experiments)
// This enables the AKS cluster as a target in Chaos Studio
#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2025-06-02-preview' existing = {
  name: last(split(aksId, '/'))
  scope: resourceGroup()
}

resource chaosTarget 'Microsoft.Chaos/targets@2024-01-01' = {
  name: 'microsoft-azurekubernetesservicechaosmesh'
  scope: aksCluster
  properties: {}
}

// Enable required capabilities for Chaos Mesh experiments
resource chaosCapabilityPodChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enablePodChaos) {
  name: 'podChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityNetworkChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableNetworkChaos) {
  name: 'networkChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityStressChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableStressChaos) {
  name: 'stressChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityIOChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableIOChaos) {
  name: 'ioChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityTimeChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableTimeChaos) {
  name: 'timeChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityKernelChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableKernelChaos) {
  name: 'kernelChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityHTTPChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableHTTPChaos) {
  name: 'httpChaos-2.2'
  parent: chaosTarget
}

resource chaosCapabilityDNSChaos 'Microsoft.Chaos/targets/capabilities@2024-01-01' = if (enableDNSChaos) {
  name: 'dnsChaos-2.2'
  parent: chaosTarget
}

// Helper: common selector and step scaffold
// The target must reference the Chaos Studio target resource
var selector = {
  id: 'aks'
  type: 'List'
  targets: [
    {
      id: chaosTarget.id
      type: 'ChaosTarget'
    }
  ]
}

// JSON specs as objects (stringify at use-site)
var podChaosSpec = {
  action: 'pod-failure'
  mode: 'one'
  duration: meshDuration
  // Note: pod-failure changes container images to pause image, making pods unavailable
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
}

var networkChaosSpec = {
  action: 'delay'
  mode: 'all'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  delay: {
    latency: '200ms'
    jitter: '50ms'
  }
  direction: 'to'
}

var networkChaosLossSpec = {
  action: 'loss'
  mode: 'all'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  direction: 'to'
  // Chaos Mesh NetworkChaos expects an object for loss spec
  // loss: percentage (string, 0-100), correlation: percentage (string, 0-100)
  loss: {
    loss: '100'
    correlation: '0'
  }
}

var stressChaosSpec = {
  mode: 'one'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  stressors: {
    cpu: { workers: 1, load: 80 }
    memory: { workers: 1, size: '256MB' }
  }
}

var ioChaosSpec = {
  action: 'latency'
  mode: 'one'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  delay: '100ms'
  path: '/data'
  percent: 100
  volumePath: '/data'
}

var timeChaosSpec = {
  mode: 'one'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  timeOffset: '300s'
  clockIds: ['CLOCK_REALTIME']
}

var kernelChaosSpec = {
  mode: 'one'
  // Note: discrete actions like kernel syscall failures don't need duration in jsonSpec
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  failKernRequest: {
    callchain: [
      { funcname: '__x64_sys_openat' }
    ]
    failtype: 0
    probability: 100
  }
}

var httpChaosSpec = {
  mode: 'one'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
  target: 'Request'
  port: 8000
  method: 'GET'
  path: '/'
  abort: true
}

var dnsChaosSpec = {
  action: 'error'
  mode: 'all'
  // Chaos Mesh supports glob-style wildcards in patterns (e.g., *.example.com).
  // Root and subdomains are matched separately; include both if required.
  // When no hosts provided, fall back to a broad match for Azure Managed Redis FQDNs.
  patterns: empty(redisHosts) ? ['*.*.redis.azure.net'] : redisHosts
  scope: 'cluster'
  duration: meshDuration
  selector: {
    namespaces: [namespace]
    labelSelectors: { app: appLabel }
  }
}

// PodChaos: make one matching pod unavailable
resource expPodChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enablePodChaos) {
  name: 'exp-aks-pod-failure'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:podChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(podChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// NetworkChaos: inject 200ms delay to matching pods
resource expNetworkChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableNetworkChaos) {
  name: 'exp-aks-network-delay'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:networkChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(networkChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// NetworkChaos (loss: 100%) - simulate complete blackhole towards targets
resource expNetworkChaosLoss 'Microsoft.Chaos/experiments@2024-01-01' = if (enableNetworkChaosLoss) {
  name: 'exp-aks-network-loss'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:networkChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(networkChaosLossSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// StressChaos: CPU + memory stress on one pod
resource expStressChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableStressChaos) {
  name: 'exp-aks-stress'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:stressChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(stressChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// IOChaos: file system delay on /tmp
resource expIOChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableIOChaos) {
  name: 'exp-aks-io'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:ioChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(ioChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// TimeChaos: skew time by +300s
resource expTimeChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableTimeChaos) {
  name: 'exp-aks-time'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:timeChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(timeChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// KernelChaos: inject kernel-level syscall failures
resource expKernelChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableKernelChaos) {
  name: 'exp-aks-kernel'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:kernelChaos/2.2'
                type: 'discrete'
                selectorId: 'aks'
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(kernelChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// HTTPChaos: inject HTTP request/response failures
resource expHTTPChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableHTTPChaos) {
  name: 'exp-aks-http'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:httpChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(httpChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// DNSChaos: inject DNS resolution failures
resource expDNSChaos 'Microsoft.Chaos/experiments@2024-01-01' = if (enableDNSChaos) {
  name: 'exp-aks-dns'
  location: location
  identity: {
    type: 'SystemAssigned'
  }
  tags: tags
  properties: {
    selectors: [selector]
    steps: [
      {
        name: 'step1'
        branches: [
          {
            name: 'branch1'
            actions: [
              {
                name: 'urn:csci:microsoft:azureKubernetesServiceChaosMesh:dnsChaos/2.2'
                type: 'continuous'
                selectorId: 'aks'
                duration: defaultDuration // FALLBACK: jsonSpec duration (meshDuration) controls execution time
                parameters: [
                  {
                    key: 'jsonSpec'
                    value: string(dnsChaosSpec)
                  }
                ]
              }
            ]
          }
        ]
      }
    ]
  }
}

// Azure Kubernetes Service Cluster Admin Role
var chaosTargetRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '0ab0b1a8-8aac-4efd-b8c2-3ee1fb270be8'
)

resource roleAssignmentPodChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enablePodChaos) {
  // Use new stable seed aligned with resource rename to avoid update attempts
  name: guid(aksId, 'exp-pod-failure', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expPodChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentNetworkChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaos) {
  // Distinct seed per variant (delay)
  name: guid(aksId, 'exp-network-delay', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expNetworkChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentNetworkChaosLoss 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaosLoss) {
  name: guid(aksId, 'exp-network-loss', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expNetworkChaosLoss!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentStressChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableStressChaos) {
  name: guid(aksId, 'exp-stress', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expStressChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentIOChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableIOChaos) {
  name: guid(aksId, 'exp-io', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expIOChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentTimeChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableTimeChaos) {
  name: guid(aksId, 'exp-time', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expTimeChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentKernelChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableKernelChaos) {
  name: guid(aksId, 'exp-kernel', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expKernelChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentHTTPChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableHTTPChaos) {
  name: guid(aksId, 'exp-http', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expHTTPChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

resource roleAssignmentDNSChaos 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableDNSChaos) {
  name: guid(aksId, 'exp-dns', chaosTargetRoleDefinitionId)
  scope: aksCluster
  properties: {
    roleDefinitionId: chaosTargetRoleDefinitionId
    principalId: expDNSChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
