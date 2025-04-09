from boto3 import client # from boto3 import client
import csv
from datetime import datetime

def get_identity_center_details():
    # Initialize AWS clients
    sso_admin = client('sso-admin')
    identity_store = client('identitystore')
    organizations = client('organizations')

    # Get Identity Center instance details
    instances = sso_admin.list_instances()['Instances']
    if not instances:
        raise RuntimeError("No IAM Identity Center instance found")
    
    instance_arn = instances[0]['InstanceArn']
    identity_store_id = instances[0]['IdentityStoreId']

    # Initialize data dictionaries
    users_dict = {}
    groups_dict = {}
    permission_sets_dict = {}
    report_data = []

    def get_users():
        """Fetch all users from Identity Store"""
        print("Fetching users...")
        paginator = identity_store.get_paginator('list_users')
        for page in paginator.paginate(IdentityStoreId=identity_store_id):
            for user in page['Users']:
                email = 'N/A'
                if 'Emails' in user and user['Emails']:
                    email = user['Emails'][0].get('Value', 'N/A')
                users_dict[user['UserId']] = {
                    'Username': user['UserName'],
                    'Email': email,
                    'UserId': user['UserId']
                }

    def get_groups():
        """Fetch all groups and their members"""
        print("Fetching groups and their members...")
        paginator = identity_store.get_paginator('list_groups')
        for page in paginator.paginate(IdentityStoreId=identity_store_id):
            for group in page['Groups']:
                group_id = group['GroupId']
                group_name = group['DisplayName']
                
                # Get group members
                members = []
                member_details = []
                try:
                    members_paginator = identity_store.get_paginator('list_group_memberships')
                    for members_page in members_paginator.paginate(
                        IdentityStoreId=identity_store_id,
                        GroupId=group_id
                    ):
                        for membership in members_page['GroupMemberships']:
                            user_id = membership['MemberId']['UserId']
                            if user_id in users_dict:
                                members.append(users_dict[user_id])
                                member_details.append({
                                    'UserId': user_id,
                                    'Username': users_dict[user_id]['Username'],
                                    'Email': users_dict[user_id]['Email']
                                })
                except Exception as e:
                    print(f"Error getting members for group {group_name}: {str(e)}")

                groups_dict[group_id] = {
                    'GroupName': group_name,
                    'Members': members,
                    'MemberDetails': member_details
                }

    def get_permission_sets():
        """Fetch all permission sets"""
        print("Fetching permission sets...")
        paginator = sso_admin.get_paginator('list_permission_sets')
        for page in paginator.paginate(InstanceArn=instance_arn):
            for permission_set_arn in page['PermissionSets']:
                try:
                    permission_set = sso_admin.describe_permission_set(
                        InstanceArn=instance_arn,
                        PermissionSetArn=permission_set_arn
                    )['PermissionSet']
                    
                    permission_sets_dict[permission_set_arn] = {
                        'Name': permission_set['Name'],
                        'Description': permission_set.get('Description', 'N/A')
                    }
                except Exception as e:
                    print(f"Error describing permission set {permission_set_arn}: {str(e)}")

    def process_account_assignments():
        """Process assignments for all accounts"""
        print("Processing account assignments...")
        paginator = organizations.get_paginator('list_accounts')
        
        for page in paginator.paginate():
            for account in page['Accounts']:
                if account['Status'] != 'ACTIVE':
                    continue

                account_id = account['Id']
                account_name = account['Name']
                account_email = account['Email']

                try:
                    # Get permission sets provisioned to this account
                    permission_sets_response = sso_admin.list_permission_sets_provisioned_to_account(
                        InstanceArn=instance_arn,
                        AccountId=account_id
                    )

                    for permission_set_arn in permission_sets_response['PermissionSets']:
                        permission_set_name = permission_sets_dict[permission_set_arn]['Name']
                        
                        # Get assignments for this permission set
                        assignments_paginator = sso_admin.get_paginator('list_account_assignments')
                        for assignments_page in assignments_paginator.paginate(
                            InstanceArn=instance_arn,
                            AccountId=account_id,
                            PermissionSetArn=permission_set_arn
                        ):
                            for assignment in assignments_page['AccountAssignments']:
                                principal_id = assignment['PrincipalId']
                                principal_type = assignment['PrincipalType']

                                if principal_type == 'USER':
                                    user_info = users_dict.get(principal_id, {})
                                    report_data.append({
                                        'Account ID': account_id,
                                        'Account Name': account_name,
                                        'Account Email': account_email,
                                        'Permission Set': permission_set_name,
                                        'Principal Type': 'User',
                                        'Principal Name': user_info.get('Username', 'Unknown'),
                                        'Principal Email': user_info.get('Email', 'N/A'),
                                        'Group Name': 'N/A',
                                        'Group Members': 'N/A',
                                        'User ID': user_info.get('UserId', 'N/A'),
                                        'User Name': user_info.get('Username', 'N/A'),
                                        'User Email': user_info.get('Email', 'N/A')
                                    })
                                else:  # GROUP
                                    group_info = groups_dict.get(principal_id, {})
                                    group_name = group_info.get('GroupName', 'Unknown')
                                    group_members = group_info.get('MemberDetails', [])
                                    
                                    # Format group members as string
                                    members_str = '; '.join([
                                        f"{member['Username']} ({member['Email']})"
                                        for member in group_members
                                    ]) if group_members else 'No members'

                                    # Create individual entries for each group member
                                    for member in group_members:
                                        report_data.append({
                                            'Account ID': account_id,
                                            'Account Name': account_name,
                                            'Account Email': account_email,
                                            'Permission Set': permission_set_name,
                                            'Principal Type': 'Group',
                                            'Principal Name': group_name,
                                            'Principal Email': 'N/A',
                                            'Group Name': group_name,
                                            'Group Members': members_str,
                                            'User ID': member['UserId'],
                                            'User Name': member['Username'],
                                            'User Email': member['Email']
                                        })

                except Exception as e:
                    print(f"Error processing account {account_id}: {str(e)}")

    # Execute all data gathering functions
    get_users()
    get_groups()
    get_permission_sets()
    process_account_assignments()

    # Generate CSV report
    timestamp = datetime.now().strftime('%Y-%m-%d_%H-%M-%S')
    filename = f'identity_center_report_{timestamp}.csv'
    headers = [
        'Account ID', 'Account Name', 'Account Email', 'Permission Set',
        'Principal Type', 'Principal Name', 'Principal Email',
        'Group Name', 'Group Members', 'User ID', 'User Name', 'User Email'
    ]

    with open(filename, 'w', newline='') as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=headers)
        writer.writeheader()
        writer.writerows(report_data)

    print(f"Report generated successfully: {filename}")
    return filename

if __name__ == "__main__":
    try:
        report_file = get_identity_center_details()
        print(f"Report generated successfully: {report_file}")
    except Exception as e:
        print(f"Error generating report: {str(e)}")
