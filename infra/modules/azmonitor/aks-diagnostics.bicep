@description('Name of the AKS cluster (extracted from resource ID in parent)')
param aksClusterName string

@description('Log Analytics workspace resource ID')
param logAnalyticsWorkspaceId string

@description('Log Analytics workspace name')
param logAnalyticsWorkspaceName string

// ADR-005: AKS diagnostic logs collected into resource-specific tables with Basic plan
resource aksCluster 'Microsoft.ContainerService/managedClusters@2024-09-01' existing = {
  name: aksClusterName
}

resource logAnalyticsWorkspace 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: logAnalyticsWorkspaceName
}

resource diagnosticSetting 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: 'aks-control-plane-basic'
  scope: aksCluster
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      {
        category: 'kube-audit-admin'
        enabled: true
      }
      {
        category: 'kube-apiserver'
        enabled: true
      }
      {
        category: 'kube-controller-manager'
        enabled: true
      }
      {
        category: 'kube-scheduler'
        enabled: true
      }
      {
        category: 'cluster-autoscaler'
        enabled: true
      }
      {
        category: 'cloud-controller-manager'
        enabled: true
      }
      {
        category: 'guard'
        enabled: true
      }
      {
        category: 'csi-azuredisk-controller'
        enabled: true
      }
      {
        category: 'csi-azurefile-controller'
        enabled: true
      }
      {
        category: 'csi-snapshot-controller'
        enabled: true
      }
    ]
  }
}

// Table plan: Basic (ADR-005)
resource aksAuditAdminTable 'Microsoft.OperationalInsights/workspaces/tables@2025-02-01' = {
  parent: logAnalyticsWorkspace
  name: 'AKSAuditAdmin'
  properties: {
    plan: 'Basic'
  }
}

resource aksControlPlaneTable 'Microsoft.OperationalInsights/workspaces/tables@2025-02-01' = {
  parent: logAnalyticsWorkspace
  name: 'AKSControlPlane'
  properties: {
    plan: 'Basic'
  }
}
