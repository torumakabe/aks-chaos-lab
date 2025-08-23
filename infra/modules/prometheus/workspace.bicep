@description('Deployment location')
param location string

@description('Tags object')
param tags object = {}

@description('Azure Monitor Workspace name')
param workspaceName string

// Azure Monitor Workspace for managed Prometheus
resource azureMonitorWorkspace 'Microsoft.Monitor/accounts@2023-04-03' = {
  name: workspaceName
  location: location
  tags: tags
}

output workspaceId string = azureMonitorWorkspace.id
output workspaceNameOut string = azureMonitorWorkspace.name
