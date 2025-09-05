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

var podChaosSpec = {
  action: 'pod-failure'
  mode: 'one'
  duration: meshDuration
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
                duration: defaultDuration
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
                duration: defaultDuration
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
                duration: defaultDuration
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
                duration: defaultDuration
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
                duration: defaultDuration
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
                duration: defaultDuration
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

// Azure Kubernetes Service RBAC Admin Role (required for Microsoft Entra authentication)
var aksRBACAdminRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '3498e952-d568-435e-9b2c-8d77e338d7f7'
)

// Azure Kubernetes Service Cluster User Role (required for Microsoft Entra authentication)
var aksClusterUserRoleDefinitionId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '4abbcc35-e782-43d8-92c5-2d3f1bd2253f'
)

// Role assignments for PodChaos experiment - RBAC Admin
resource roleAssignmentPodChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enablePodChaos) {
  name: guid(aksId, 'exp-pod-failure', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expPodChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for PodChaos experiment - Cluster User
resource roleAssignmentPodChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enablePodChaos) {
  name: guid(aksId, 'exp-pod-failure', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expPodChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for NetworkChaos experiment - RBAC Admin
resource roleAssignmentNetworkChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaos) {
  name: guid(aksId, 'exp-network-delay', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expNetworkChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for NetworkChaos experiment - Cluster User
resource roleAssignmentNetworkChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaos) {
  name: guid(aksId, 'exp-network-delay', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expNetworkChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for NetworkChaos Loss experiment - RBAC Admin
resource roleAssignmentNetworkChaosLossRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaosLoss) {
  name: guid(aksId, 'exp-network-loss', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expNetworkChaosLoss!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for NetworkChaos Loss experiment - Cluster User
resource roleAssignmentNetworkChaosLossClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableNetworkChaosLoss) {
  name: guid(aksId, 'exp-network-loss', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expNetworkChaosLoss!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for StressChaos experiment - RBAC Admin
resource roleAssignmentStressChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableStressChaos) {
  name: guid(aksId, 'exp-stress', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expStressChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for StressChaos experiment - Cluster User
resource roleAssignmentStressChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableStressChaos) {
  name: guid(aksId, 'exp-stress', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expStressChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for IOChaos experiment - RBAC Admin
resource roleAssignmentIOChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableIOChaos) {
  name: guid(aksId, 'exp-io', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expIOChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for IOChaos experiment - Cluster User
resource roleAssignmentIOChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableIOChaos) {
  name: guid(aksId, 'exp-io', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expIOChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for TimeChaos experiment - RBAC Admin
resource roleAssignmentTimeChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableTimeChaos) {
  name: guid(aksId, 'exp-time', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expTimeChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for TimeChaos experiment - Cluster User
resource roleAssignmentTimeChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableTimeChaos) {
  name: guid(aksId, 'exp-time', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expTimeChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for KernelChaos experiment - RBAC Admin
resource roleAssignmentKernelChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableKernelChaos) {
  name: guid(aksId, 'exp-kernel', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expKernelChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for KernelChaos experiment - Cluster User
resource roleAssignmentKernelChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableKernelChaos) {
  name: guid(aksId, 'exp-kernel', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expKernelChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for HTTPChaos experiment - RBAC Admin
resource roleAssignmentHTTPChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableHTTPChaos) {
  name: guid(aksId, 'exp-http', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expHTTPChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for HTTPChaos experiment - Cluster User
resource roleAssignmentHTTPChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableHTTPChaos) {
  name: guid(aksId, 'exp-http', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expHTTPChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for DNSChaos experiment - RBAC Admin
resource roleAssignmentDNSChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableDNSChaos) {
  name: guid(aksId, 'exp-dns', 'rbac-admin')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: expDNSChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Role assignments for DNSChaos experiment - Cluster User
resource roleAssignmentDNSChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (enableDNSChaos) {
  name: guid(aksId, 'exp-dns', 'cluster-user')
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: expDNSChaos!.identity.principalId
    principalType: 'ServicePrincipal'
  }
}
