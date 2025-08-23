@description('Deployment location')
param location string

@description('User Assigned Managed Identity name')
param identityName string

@description('Tags object')
param tags object = {}

@description('AKS OIDC issuer URL')
param oidcIssuerUrl string

@description('Kubernetes service account namespace for federated credential')
param serviceAccountNamespace string

@description('Kubernetes service account name for federated credential')
param serviceAccountName string

@description('OIDC audience')
param audience string = 'api://AzureADTokenExchange'

var federatedIdentitySubject = 'system:serviceaccount:${serviceAccountNamespace}:${serviceAccountName}'

resource userAssignedManagedIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: identityName
  location: location
  tags: tags
}

resource federatedIdentityCredential 'Microsoft.ManagedIdentity/userAssignedIdentities/federatedIdentityCredentials@2023-01-31' = {
  name: 'k8s-${serviceAccountNamespace}-${serviceAccountName}'
  parent: userAssignedManagedIdentity
  properties: {
    issuer: oidcIssuerUrl
    subject: federatedIdentitySubject
    audiences: [audience]
  }
}

output clientId string = userAssignedManagedIdentity.properties.clientId
output principalId string = userAssignedManagedIdentity.properties.principalId
