# Generated by Django 5.0.11 on 2025-03-27 09:45

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('steps', '0005_energysystemdesign_shortage_settings_is_selected'),
    ]

    operations = [
        migrations.AlterField(
            model_name='customdemand',
            name='high',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='customdemand',
            name='low',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='customdemand',
            name='middle',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='customdemand',
            name='very_high',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='customdemand',
            name='very_low',
            field=models.FloatField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='connection_cable_capex',
            field=models.FloatField(blank=True, db_column='connection_cable__capex', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='connection_cable_lifetime',
            field=models.PositiveSmallIntegerField(blank=True, db_column='connection_cable__lifetime', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='connection_cable_max_length',
            field=models.FloatField(blank=True, db_column='connection_cable__max_length', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='distribution_cable_capex',
            field=models.FloatField(blank=True, db_column='distribution_cable__capex', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='distribution_cable_lifetime',
            field=models.PositiveSmallIntegerField(blank=True, db_column='distribution_cable__lifetime', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='distribution_cable_max_length',
            field=models.FloatField(blank=True, db_column='distribution_cable__max_length', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='include_shs',
            field=models.BooleanField(db_column='shs__include'),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='mg_connection_cost',
            field=models.FloatField(blank=True, db_column='mg__connection_cost', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='pole_capex',
            field=models.FloatField(blank=True, db_column='pole__capex', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='pole_lifetime',
            field=models.PositiveSmallIntegerField(blank=True, db_column='pole__lifetime', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='pole_max_n_connections',
            field=models.PositiveSmallIntegerField(blank=True, db_column='pole__max_n_connections', null=True),
        ),
        migrations.AlterField(
            model_name='griddesign',
            name='shs_max_grid_cost',
            field=models.FloatField(blank=True, db_column='shs__max_grid_cost', null=True),
        ),
    ]
