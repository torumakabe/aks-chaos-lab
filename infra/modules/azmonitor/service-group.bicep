targetScope = 'tenant'

@description('Globally unique Azure Service Group name')
param serviceGroupName string

@description('Display name of the Azure Service Group')
param serviceGroupDisplayName string

@description('Parent service group resource ID. Leave empty to use the tenant root service group.')
param parentServiceGroupResourceId string = ''

@description('Existing Service Group resource ID. When set, this module skips Service Group creation and returns this existing scope.')
param existingServiceGroupResourceId string = ''

var normalizedParentServiceGroupResourceId = parentServiceGroupResourceId == 'none' ? '' : parentServiceGroupResourceId
var normalizedExistingServiceGroupResourceId = existingServiceGroupResourceId == 'none' ? '' : existingServiceGroupResourceId
var useExistingServiceGroup = !empty(normalizedExistingServiceGroupResourceId)
var serviceGroupParentResourceId = empty(normalizedParentServiceGroupResourceId)
  ? '/providers/Microsoft.Management/serviceGroups/${tenant().tenantId}'
  : normalizedParentServiceGroupResourceId
var targetServiceGroupName = useExistingServiceGroup ? last(split(normalizedExistingServiceGroupResourceId, '/')) : serviceGroupName

resource serviceGroup 'Microsoft.Management/serviceGroups@2024-02-01-preview' = if (!useExistingServiceGroup) {
  name: serviceGroupName
  properties: {
    displayName: serviceGroupDisplayName
    parent: {
      resourceId: serviceGroupParentResourceId
    }
  }
}

#disable-next-line BCP318
output serviceGroupId string = useExistingServiceGroup ? normalizedExistingServiceGroupResourceId : serviceGroup.id
output serviceGroupNameOut string = targetServiceGroupName
