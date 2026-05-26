-- ############################ Dashboard Data Function #########################
create or REPLACE FUNCTION get_dashboard_data () returns json as $$
declare
   output_json json;
begin
    SELECT
    json_build_object(
        'total_cattle',
        (
            SELECT
                count(*)
            FROM
                cattle_data
            WHERE
                new_is_currently_present = 1
        ),
        'all_cattle_data',
        (
            select count(*) from cattle_data
        ),
        'total_bull', (
            SELECT count(*) FROM cattle_data WHERE animal_type = 'BULL'
        ),
        'total_ox',
        (
            SELECT count(*) FROM cattle_data WHERE animal_type = 'OX'
        ),
        'total_female_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'female'
        ),
        'total_male_cattle',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.gender) = 'male'
        ),
        'total_female_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'female calf'
        ),
        'total_male_calf',
        (
            SELECT
                count(*)
            from
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                and lower(cd.animal_type) = 'male calf'
        ),
        'total_milking_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currenlty_milking = 1
                AND cd.new_is_currently_present = 1
        ),
        'total_pregnant_cow',
        (
            SELECT
                count(*)
            FROM
                cattle_data cd
            WHERE
                cd.new_is_currently_present = 1
                AND cd.new_is_currently_pregnant = 1
        ),
        'source_breakdown',
        (
            SELECT
                json_agg(source_breakdown) as source_breakdown
            FROM
                (
                    SELECT
                        acquisition_type,
                        count(*) as total_cattle
                    FROM
                        cattle_data
                    WHERE
                        new_is_currently_present = 1
                    GROUP BY
                        acquisition_type
                    ORDER BY
                        total_cattle desc
                ) as source_breakdown
        ),
        'generation',
        (
            SELECT
                json_agg(gen)
            FROM
                (
                    select
                        sub.generation_level as generation,
                        count(*) as total_cattle
                    FROM
                        (
                            WITH RECURSIVE
                                cattle_lineage AS (
                                    -- 1. Anchor Member
                                    SELECT
                                        cd.*,
                                        1 AS generation_level
                                    FROM
                                        cattle_data cd
                                    WHERE
                                        cd.mother_tag_number IS NULL
                                        AND cd.father_tag_number IS NULL
                                    UNION ALL
                                    -- 2. Recursive Member
                                    SELECT
                                        child.*,
                                        parent.generation_level + 1 AS generation_level
                                    FROM
                                        cattle_data child
                                        INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                                )
                            SELECT
                                *
                            FROM
                                cattle_lineage
                        ) sub
                    GROUP by
                        sub.generation_level
                    ORDER by
                        sub.generation_level asc
                ) as gen
        ),
        'top_10_milking_cattle',
        (
            select
                json_agg(top_10)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        round(top_milk.total_milk) as "total_milk"
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 milk subquery
                            SELECT
                                cml.tag_number,
                                SUM(cml.milk) AS total_milk
                            FROM
                                public.cattle_milk_logs AS cml
                            GROUP BY
                                cml.tag_number
                            ORDER BY
                                total_milk DESC
                            LIMIT
                                10
                        ) AS top_milk ON cl.tag_number = top_milk.tag_number
                    ORDER BY
                        top_milk.total_milk DESC
                ) as top_10
        ),
        'top_10_fit_cattle',
        (
            select
                json_agg(physical_good)
            from
                (
                    WITH RECURSIVE
                        cattle_lineage AS (
                            -- 1. Anchor Member (The First Generation)
                            SELECT
                                cd.*,
                                1 AS generation_level
                            FROM
                                cattle_data cd
                            WHERE
                                cd.mother_tag_number IS NULL
                                AND cd.father_tag_number IS NULL
                            UNION ALL
                            -- 2. Recursive Member
                            SELECT
                                child.*,
                                parent.generation_level + 1 AS generation_level
                            FROM
                                cattle_data child
                                INNER JOIN cattle_lineage parent ON child.mother_tag_number = parent.tag_number
                        )
                    SELECT
                        cl.tag_number,
                        cl.name,
                        cl.generation_level AS generation,
                        top_scores.hip_width,
                        top_scores.total_score
                    FROM
                        cattle_lineage AS cl
                        INNER JOIN (
                            -- Your top 10 scoring subquery
                            SELECT
                                tag_number,
                                hip_width,
                                (
                                    COALESCE(head, 0) + COALESCE(ear, 0) + COALESCE(eye, 0) + COALESCE(muzzle, 0) + COALESCE(horn, 0) + COALESCE(skin, 0) + COALESCE(tail, 0) + COALESCE(hump, 0) + COALESCE(udder, 0) + COALESCE(teat, 0) + COALESCE(dewlap, 0) + COALESCE(milk_vein, 0)
                                ) AS total_score
                            FROM
                                cattle_physical_logs
                            ORDER BY
                                total_score DESC
                            LIMIT
                                10
                        ) AS top_scores ON cl.tag_number = top_scores.tag_number
                    ORDER BY
                        top_scores.total_score DESC
                ) physical_good
        ),
        'month_wise_milk_production',
        (
            select json_agg(monthwisemilk) as monthwisemilk
            from (
                SELECT 
                    TO_CHAR(date, 'YYYY-MM') AS month,
                    round(SUM(milk)) AS total_milk
                FROM 
                    cattle_milk_logs
                GROUP BY 
                    TO_CHAR(date, 'YYYY-MM')
                ORDER BY 
                    month ASC
            ) as monthwisemilk
        )
    ) into output_json;

    return output_json;

end;
$$ language plpgsql;

select
    *
from
    get_dashboard_data ();

-- drop function get_dashboard_data();
-- ############################ cattle nested tree function #########################
CREATE OR REPLACE FUNCTION get_nested_cattle_tree (target_tag TEXT) RETURNS JSON AS $$
DECLARE
    tree_json JSON;
BEGIN
    SELECT json_build_object(
        'tag_number', c.tag_number,
        'name', c.name,
        'date_of_birth', c.date_of_birth,
        -- The recursive part: It calls itself to find and nest the next generation
        'children', (
            SELECT COALESCE(json_agg(get_nested_cattle_tree(child.tag_number)), '[]'::json)
            FROM cattle_data child
            WHERE child.mother_tag_number = c.tag_number 
               OR child.father_tag_number = c.tag_number
        )
    ) INTO tree_json
    FROM cattle_data c
    WHERE c.tag_number = target_tag;

    RETURN tree_json;
END;
$$ LANGUAGE plpgsql;

-- drop function get_nested_cattle_tree();
-- ########################## get all present cattle function ##########################
create or REPLACE FUNCTION get_all_present_cattle () returns JSON AS $$
declare
    json_data json;
begin
    select
        json_agg(present_cattle)
    from
        (
            select
                tag_number,
                name,
                gender,
                acquisition_type,
                animal_type,
                new_is_currenlty_milking as is_milking,
                new_is_currently_pregnant as is_pregnant
            from
                cattle_data
            where
                new_is_currently_present = 1
        ) as present_cattle into json_data;
    return json_data;
end;
$$ language plpgsql;

drop function get_all_present_cattle ();

-- ########################## get all milking cattle function ##########################
create or REPLACE FUNCTION get_all_milking_cattle () returns JSON AS $$
declare
    json_data json;
begin
    select
        json_agg(present_cattle)
    from
        (
            select
                tag_number,
                name,
                gender,
                acquisition_type,
                animal_type,
                new_is_currenlty_milking as is_milking,
                new_is_currently_pregnant as is_pregnant
            from
                cattle_data
            where
                new_is_currently_present = 1
                and new_is_currenlty_milking = 1
        ) as present_cattle into json_data;
    return json_data;
end;
$$ language plpgsql;

-- drop function get_all_milking_cattle();
-- ########## cattle profile function ##########
create or replace function get_cattle_profile (target_tag text) returns json as $$
declare
    profile_json json;
begin
    with recursive cattle_lineage as (
        select
            tag_number,
            mother_tag_number,
            father_tag_number,
            name,
            date_of_birth,
            new_is_currently_present,
            1 as generation_level
        from cattle_data
        where mother_tag_number is null and father_tag_number is null
        union all
        select
            child.tag_number,
            child.mother_tag_number,
            child.father_tag_number,
            child.name,
            child.date_of_birth,
            child.new_is_currently_present,
            parent.generation_level + 1
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    ),
    overview_cte as (
        select
            cl.name,
            cl.tag_number,
            cl.generation_level as generation,
            cl.date_of_birth,
            cl.new_is_currently_present as is_present,
            (select count(*) from cattle_data child where child.mother_tag_number = cl.tag_number) as total_number_of_children,
            (select count(*) from cattle_data sibling where sibling.mother_tag_number = cl.mother_tag_number and sibling.tag_number != cl.tag_number) as total_number_of_siblings,
            (select name from cattle_data where tag_number = cl.mother_tag_number) as mother_name,
            (select name from cattle_data where tag_number = cl.father_tag_number) as father_name
        from cattle_lineage cl
        where cl.tag_number = target_tag
    ),
    children_cte as (
        select
            cl.name,
            cl.tag_number,
            cl.generation_level as generation,
            cl.date_of_birth,
            (select count(*) from cattle_data grandchild where grandchild.mother_tag_number = cl.tag_number) as total_number_of_children,
            (select count(*) from cattle_data sibling where sibling.mother_tag_number = cl.mother_tag_number and sibling.tag_number != cl.tag_number) as total_number_of_siblings,
            (select name from cattle_data where tag_number = cl.mother_tag_number) as mother_name,
            (select name from cattle_data where tag_number = cl.father_tag_number) as father_name
        from cattle_lineage cl
        where cl.mother_tag_number = target_tag or cl.father_tag_number = target_tag
    ),
    physical_cte as (
        select
            hip_width,
            head as head_score,
            ear as ear_score,
            eye as eye_score,
            muzzle as muzzle_score,
            horn as horn_score,
            skin as skin_score,
            tail as tail_score,
            hump as hump_score,
            udder as udder_score,
            teat as teat_score,
            dewlap as dewlap_score,
            milk_vein as milk_vein_score
        from cattle_physical_logs
        where tag_number = target_tag
        -- order by date desc
        limit 1
    ),
    milk_cte as (
        select
            to_char(date, 'YYYY-MM') as month,
            round(sum(milk)::numeric) as milk
        from cattle_milk_logs
        where tag_number = target_tag
        group by to_char(date, 'YYYY-MM')
        order by month asc
    ),
    siblings_cte as (
        select
            s.name,
            s.tag_number,
            s.date_of_birth
        from cattle_data s
        where s.mother_tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag)
          and s.tag_number != target_tag
    ),
    family_cte as (
        select
            (select name from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag)) as mother_name,
            (select name from cattle_data where tag_number = (select father_tag_number from cattle_data where tag_number = target_tag)) as father_name,
            (select name from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag))) as grand_mother_name,
            (select name from cattle_data where tag_number = (select father_tag_number from cattle_data where tag_number = (select mother_tag_number from cattle_data where tag_number = target_tag))) as grand_father_name
    )
    select json_build_object(
        'overview', (
            select json_build_object(
                'name', o.name,
                'tag_number', o.tag_number,
                'generation', o.generation,
                'date_of_birth', o.date_of_birth,
                'is_present', o.is_present,
                'total_number_of_children', o.total_number_of_children,
                'total_number_of_siblings', o.total_number_of_siblings,
                'mother_name', o.mother_name,
                'father_name', o.father_name,
                'children', coalesce((select json_agg(json_build_object(
                    'name', c.name,
                    'tag_number', c.tag_number,
                    'generation', c.generation,
                    'date_of_birth', c.date_of_birth,
                    'total_number_of_children', c.total_number_of_children,
                    'total_number_of_siblings', c.total_number_of_siblings,
                    'mother_name', c.mother_name,
                    'father_name', c.father_name
                )) from children_cte c), '[]'::json),
                'physical_data', (
                    select row_to_json(p.*) from physical_cte p
                )
            ) from overview_cte o
        ),
        'milk_logs', coalesce((select json_agg(json_build_object(
            'month', m.month,
            'milk', m.milk
        )) from milk_cte m), '[]'::json),
        'family_tree', (
            select json_build_object(
                'mother_name', f.mother_name,
                'father_name', f.father_name,
                'grand_mother_name', f.grand_mother_name,
                'grand_father_name', f.grand_father_name,
                'siblings', coalesce((select json_agg(json_build_object(
                    'name', s.name,
                    'tag_number', s.tag_number,
                    'date_of_birth', s.date_of_birth
                )) from siblings_cte s), '[]'::json)
            ) from family_cte f
        )
    ) into profile_json;

    return profile_json;
end;
$$ language plpgsql;

-- drop function get_cattle_profile();
-- ########## genealogy all cattle ##########
-- ########################## get donated out cattle function ##########################
create or replace function get_donated_out_cattle () returns json as $$
declare
    json_data json;
begin
    select
        json_agg(donated)
    from (
        select
            d.name,
            d.tag_number,
            d.donated_out_date,
            d.donated_to,
            d.mobile_number,
            d.animal_type,
            cd.gender
        from donated_out d
        left join cattle_data cd on cd.tag_number = d.tag_number
        order by d.donated_out_date desc nulls last
    ) as donated into json_data;
    return json_data;
end;
$$ language plpgsql;

-- drop function get_donated_out_cattle();
-- ########## genealogy all cattle ##########
create or replace function get_all_cattle_for_genealogy () returns json as $$
declare
    json_data json;
begin
    with recursive cattle_lineage as (
        select
            cd.*,
            1 as generation_level
        from cattle_data cd
        where cd.mother_tag_number is null
        union all
        select
            child.*,
            parent.generation_level + 1 as generation_level
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    )
    select
        json_agg(genealogy_data)
    from (
        select
            tag_number,
            name,
            gender,
            animal_type,
            acquisition_type,
            date_of_birth::text as date_of_birth,
            new_is_currently_present as is_present,
            new_is_currenlty_milking as is_milking,
            new_is_currently_pregnant as is_pregnant,
            mother_tag_number,
            father_tag_number,
            generation_level as generation
        from cattle_lineage
        order by generation_level asc, name asc
    ) as genealogy_data into json_data;
    return json_data;
end;
$$ language plpgsql;

-- ########## cattle card data function ##########
create or replace function get_cattle_card_data (target_tag text) returns json as $$
declare
    result_json json;
begin
    with recursive cattle_lineage as (
        select
            cd.id, cd.name, cd.tag_number, cd.acquisition_type,
            cd.date_of_birth, cd.animal_type, cd.mother_name,
            cd.mother_tag_number, cd.father_name, cd.father_tag_number,
            cd.new_is_currently_present, cd.new_is_currently_pregnant,
            cd.new_is_currenlty_milking, cd.weight_at_birth, cd.gender,
            1::int as generation_level
        from cattle_data cd
        where cd.mother_tag_number is null and cd.father_tag_number is null
        union all
        select
            child.id, child.name, child.tag_number, child.acquisition_type,
            child.date_of_birth, child.animal_type, child.mother_name,
            child.mother_tag_number, child.father_name, child.father_tag_number,
            child.new_is_currently_present, child.new_is_currently_pregnant,
            child.new_is_currenlty_milking, child.weight_at_birth, child.gender,
            (parent.generation_level + 1)::int as generation_level
        from cattle_data child
        inner join cattle_lineage parent on child.mother_tag_number = parent.tag_number
    ),
    me as (
        select * from cattle_lineage where tag_number = target_tag
    ),
    phys as (
        select * from cattle_physical_logs where tag_number = target_tag order by id desc limit 1
    )
    select json_build_object(
        'overview', json_build_object(
            'name', me.name,
            'tag_number', me.tag_number,
            'physical_score', (select round((coalesce(head::numeric,0)+coalesce(ear::numeric,0)+coalesce(eye::numeric,0)+coalesce(muzzle::numeric,0)+coalesce(horn::numeric,0)+coalesce(skin::numeric,0)+coalesce(tail::numeric,0)+coalesce(hump::numeric,0)+coalesce(udder::numeric,0)+coalesce(teat::numeric,0)+coalesce(dewlap::numeric,0)+coalesce(milk_vein::numeric,0))/12.0, 1) from phys),
            'average_physical_score', (select round((coalesce(avg(head)::numeric,0)+coalesce(avg(ear)::numeric,0)+coalesce(avg(eye)::numeric,0)+coalesce(avg(muzzle)::numeric,0)+coalesce(avg(horn)::numeric,0)+coalesce(avg(skin)::numeric,0)+coalesce(avg(tail)::numeric,0)+coalesce(avg(hump)::numeric,0)+coalesce(avg(udder)::numeric,0)+coalesce(avg(teat)::numeric,0)+coalesce(avg(dewlap)::numeric,0)+coalesce(avg(milk_vein)::numeric,0))/12.0, 1) from cattle_physical_logs),
            'acquisition_type', coalesce(me.acquisition_type, 'Not available'),
            'generation', coalesce(me.generation_level::text, 'Not available'),
            'DOB', coalesce(me.date_of_birth, 'Not available'),
            'total_childrens', (select count(*) from cattle_data where mother_tag_number = target_tag),
            'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
            'is_present', me.new_is_currently_present,
            'lactation_cycle', case
                when me.new_is_currenlty_milking = 1 then 'Lactating'
                when me.new_is_currently_pregnant = 1 then 'Pregnant'
                when (select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1) is not null then 'Post-lactation'
                else 'Not available'
            end,
            'last_calving_date', coalesce((select birth_date from cattle_pragnancies_logs where tag_number = target_tag and birth_date is not null order by birth_date desc limit 1), 'Not available'),
            'mother', case when me.mother_tag_number is not null and (select name from cattle_lineage where tag_number = me.mother_tag_number) is not null then (select json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) from cattle_lineage m where m.tag_number = me.mother_tag_number) else to_json('Not available'::text) end,
            'father', case when me.father_tag_number is not null and (select name from cattle_lineage where tag_number = me.father_tag_number) is not null then (select json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) from cattle_lineage f where f.tag_number = me.father_tag_number) else to_json('Not available'::text) end,
            'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json),
            'breed_score', json_build_object(
                'hip_width', coalesce((select hip_width from phys), '0'),
                'head', coalesce((select head::text from phys), '0'),
                'ear', coalesce((select ear::text from phys), '0'),
                'eye', coalesce((select eye::text from phys), '0'),
                'muzzle', coalesce((select muzzle::text from phys), '0'),
                'horn', coalesce((select horn::text from phys), '0'),
                'skin', coalesce((select skin::text from phys), '0'),
                'tail', coalesce((select tail::text from phys), '0'),
                'hump', coalesce((select hump::text from phys), '0'),
                'udder', coalesce((select udder::text from phys), '0'),
                'teat', coalesce((select teat::text from phys), '0'),
                'dewlap', coalesce((select dewlap::text from phys), '0'),
                'milk_vein', coalesce((select milk_vein::text from phys), '0')
            ),
            'weight', case when me.weight_at_birth is not null then me.weight_at_birth::text else 'Not available' end,
            'age', case
                when me.date_of_birth is not null and me.date_of_birth ~ '^\d{4}-\d{2}-\d{2}' then
                    extract(year from age(now(), me.date_of_birth::date))::text || ' years'
                else 'Not available'
            end,
            'average_milk_per_day', case when me.new_is_currenlty_milking = 1 then (select round(avg(milk)::numeric, 1) from cattle_milk_logs where tag_number = target_tag) else null end
        ),
        'milk_by_month', case when me.new_is_currenlty_milking = 1
            then coalesce((select json_agg(json_build_object('date', month_group, 'milk', total_milk)) from (
                select to_char(date::timestamp, 'YYYY-MM') as month_group, round(sum(milk)::numeric, 1) as total_milk
                from cattle_milk_logs where tag_number = target_tag
                group by to_char(date::timestamp, 'YYYY-MM')
                order by month_group
            ) sub), '[]'::json)
            else '[]'::json
        end,
        'milk_by_day_only_for_month', case when me.new_is_currenlty_milking = 1
            then coalesce((select json_agg(json_build_object('date', milk_date, 'milk', round(milk::numeric, 1))) from (
                select date as milk_date, milk from cattle_milk_logs where tag_number = target_tag order by milk_date desc limit 30
            ) sub), '[]'::json)
            else '[]'::json
        end,
        'family', json_build_object(
            'mother', (select case when m.name is not null then json_build_object('name', m.name, 'tag_number', m.tag_number, 'generation', m.generation_level) else null::json end from cattle_lineage m where m.tag_number = me.mother_tag_number),
            'father', (select case when f.name is not null then json_build_object('name', f.name, 'tag_number', f.tag_number, 'generation', f.generation_level) else null::json end from cattle_lineage f where f.tag_number = me.father_tag_number),
            'siblings', coalesce((select json_agg(json_build_object('name', sib.name, 'tag_number', sib.tag_number, 'generation', sib.generation_level)) from cattle_lineage sib where sib.mother_tag_number = me.mother_tag_number and sib.tag_number != target_tag), '[]'::json),
            'childrens', coalesce((select json_agg(json_build_object('name', c.name, 'tag_number', c.tag_number, 'generation', c.generation_level)) from cattle_lineage c where c.mother_tag_number = target_tag), '[]'::json)
        )
    ) into result_json
    from me;

    return result_json;
end;
$$ language plpgsql;


select from get_cattle_card_data('SOM-052');