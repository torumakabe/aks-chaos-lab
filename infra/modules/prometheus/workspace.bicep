@description('Deployment location')
param location string

@description('Tags object')
param tags object = {}

@description('Azure Monitor Workspace name')
param workspaceName string

resource azureMonitorWorkspace 'Microsoft.Monitor/accounts@2025-05-03-preview' = {
  name: workspaceName
  location: location
  tags: tags
  properties: {
    // Microsoft Learn documents this preview property as deployable, but the current Bicep type marks it read-only.
    #disable-next-line BCP073
    metrics: {
      enableAccessUsingResourcePermissions: true
    }
  }
}

output workspaceId string = azureMonitorWorkspace.id
output workspaceNameOut string = azureMonitorWorkspace.name
