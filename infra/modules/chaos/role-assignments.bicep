@description('Target AKS cluster resource ID')
param aksId string

@description('Principal ID for PodChaos experiment')
param podChaosPrincipalId string = ''

@description('Principal ID for NetworkChaos experiment')
param networkChaosPrincipalId string = ''

@description('Principal ID for NetworkChaos Loss experiment')
param networkChaosLossPrincipalId string = ''

@description('Principal ID for StressChaos experiment')
param stressChaosPrincipalId string = ''

@description('Principal ID for IOChaos experiment')
param ioChaosPrincipalId string = ''

@description('Principal ID for TimeChaos experiment')
param timeChaosPrincipalId string = ''

@description('Principal ID for KernelChaos experiment')
param kernelChaosPrincipalId string = ''

@description('Principal ID for HTTPChaos experiment')
param httpChaosPrincipalId string = ''

@description('Principal ID for DNSChaos experiment')
param dnsChaosPrincipalId string = ''

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

#disable-next-line BCP081
resource aksCluster 'Microsoft.ContainerService/managedClusters@2025-08-02-preview' existing = {
  name: last(split(aksId, '/'))
  scope: resourceGroup()
}

// PodChaos - RBAC Admin
resource roleAssignmentPodChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(podChaosPrincipalId)) {
  name: guid(aksId, 'exp-pod-failure', 'rbac-admin', podChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: podChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// PodChaos - Cluster User
resource roleAssignmentPodChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(podChaosPrincipalId)) {
  name: guid(aksId, 'exp-pod-failure', 'cluster-user', podChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: podChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// NetworkChaos - RBAC Admin
resource roleAssignmentNetworkChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(networkChaosPrincipalId)) {
  name: guid(aksId, 'exp-network-delay', 'rbac-admin', networkChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: networkChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// NetworkChaos - Cluster User
resource roleAssignmentNetworkChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(networkChaosPrincipalId)) {
  name: guid(aksId, 'exp-network-delay', 'cluster-user', networkChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: networkChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// NetworkChaos Loss - RBAC Admin
resource roleAssignmentNetworkChaosLossRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(networkChaosLossPrincipalId)) {
  name: guid(aksId, 'exp-network-loss', 'rbac-admin', networkChaosLossPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: networkChaosLossPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// NetworkChaos Loss - Cluster User
resource roleAssignmentNetworkChaosLossClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(networkChaosLossPrincipalId)) {
  name: guid(aksId, 'exp-network-loss', 'cluster-user', networkChaosLossPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: networkChaosLossPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// StressChaos - RBAC Admin
resource roleAssignmentStressChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(stressChaosPrincipalId)) {
  name: guid(aksId, 'exp-stress', 'rbac-admin', stressChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: stressChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// StressChaos - Cluster User
resource roleAssignmentStressChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(stressChaosPrincipalId)) {
  name: guid(aksId, 'exp-stress', 'cluster-user', stressChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: stressChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// IOChaos - RBAC Admin
resource roleAssignmentIOChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(ioChaosPrincipalId)) {
  name: guid(aksId, 'exp-io', 'rbac-admin', ioChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: ioChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// IOChaos - Cluster User
resource roleAssignmentIOChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(ioChaosPrincipalId)) {
  name: guid(aksId, 'exp-io', 'cluster-user', ioChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: ioChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// TimeChaos - RBAC Admin
resource roleAssignmentTimeChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(timeChaosPrincipalId)) {
  name: guid(aksId, 'exp-time', 'rbac-admin', timeChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: timeChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// TimeChaos - Cluster User
resource roleAssignmentTimeChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(timeChaosPrincipalId)) {
  name: guid(aksId, 'exp-time', 'cluster-user', timeChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: timeChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// KernelChaos - RBAC Admin
resource roleAssignmentKernelChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(kernelChaosPrincipalId)) {
  name: guid(aksId, 'exp-kernel', 'rbac-admin', kernelChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: kernelChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// KernelChaos - Cluster User
resource roleAssignmentKernelChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(kernelChaosPrincipalId)) {
  name: guid(aksId, 'exp-kernel', 'cluster-user', kernelChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: kernelChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// HTTPChaos - RBAC Admin
resource roleAssignmentHTTPChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(httpChaosPrincipalId)) {
  name: guid(aksId, 'exp-http', 'rbac-admin', httpChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: httpChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// HTTPChaos - Cluster User
resource roleAssignmentHTTPChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(httpChaosPrincipalId)) {
  name: guid(aksId, 'exp-http', 'cluster-user', httpChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: httpChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// DNSChaos - RBAC Admin
resource roleAssignmentDNSChaosRBACAdmin 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(dnsChaosPrincipalId)) {
  name: guid(aksId, 'exp-dns', 'rbac-admin', dnsChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksRBACAdminRoleDefinitionId
    principalId: dnsChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// DNSChaos - Cluster User
resource roleAssignmentDNSChaosClusterUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(dnsChaosPrincipalId)) {
  name: guid(aksId, 'exp-dns', 'cluster-user', dnsChaosPrincipalId)
  scope: aksCluster
  properties: {
    roleDefinitionId: aksClusterUserRoleDefinitionId
    principalId: dnsChaosPrincipalId
    principalType: 'ServicePrincipal'
  }
}
