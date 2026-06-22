@description('Deployment location')
param location string

@description('Tags object')
param tags object = {}

@description('Azure Monitor Workspace name')
param workspaceName string

resource azureMonitorWorkspace 'Microsoft.Monitor/accounts@2025-10-03' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    metrics: {
      enableAccessUsingResourcePermissions: true
    }
  }
}

output workspaceId string = azureMonitorWorkspace.id
output workspaceNameOut string = azureMonitorWorkspace.name
