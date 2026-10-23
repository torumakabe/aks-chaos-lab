targetScope = 'subscription'

@description('Principal ID of the Azure SRE Agent user-assigned managed identity')
param principalId string

var monitoringContributorRoleId = subscriptionResourceId(
  'Microsoft.Authorization/roleDefinitions',
  '749f88d5-cbae-40b8-bcfc-e573ddc772fa'
)

resource monitoringContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(subscription().id, principalId, monitoringContributorRoleId)
  scope: subscription()
  properties: {
    roleDefinitionId: monitoringContributorRoleId
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
