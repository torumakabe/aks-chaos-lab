@description('Deployment location')
param location string

@description('Tags object')
param tags object = {}

@description('User-assigned managed identity name for Azure Monitor SLI')
param identityName string

resource sliIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: identityName
  location: location
  tags: tags
}

output identityId string = sliIdentity.id
output principalId string = sliIdentity.properties.principalId
output clientId string = sliIdentity.properties.clientId
